"""Exercise board audio and stop logic locally without native ASR or a robot."""
import ast
import asyncio
import fractions
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave
import runpy
from types import SimpleNamespace

import av
import numpy as np
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError


def board_parts():
    tree = ast.parse(Path(__file__).with_name("voice_control_board_runtime.py").read_text(encoding="utf-8"))
    names = {"QueueAudioTrack", "_joystick_walk", "transcribe_wav"}
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    env = dict(asyncio=asyncio, fractions=fractions, time=time, av=av, np=np, wave=wave,
               AudioStreamTrack=AudioStreamTrack, MediaStreamError=MediaStreamError,
               RATE=48000, PTIME_SAMPLES=960, RTC_TOPIC={"WIRELESS_CONTROLLER": "joystick"})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "board-parts", "exec"), env)
    return env


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_does_not_create_loop_bound_lock(self):
        path = Path(__file__).with_name("voice_control_board_runtime.py")
        with patch.dict("sys.modules", {"sherpa_onnx": SimpleNamespace()}):
            with patch("asyncio.Lock", side_effect=AssertionError("lock created during import")):
                env = runpy.run_path(str(path), run_name="board_import_test")
        self.assertIsNone(env["_tts_lock"])
        # The lock is created only when a speech coroutine runs.
        await env["dog_say"](None, "test")
        self.assertIsInstance(env["dog_say"].__globals__["_tts_lock"], asyncio.Lock)

    async def test_melo_rate_audio_conversion_and_timeline(self):
        env = board_parts()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "audio.wav")
            samples = (np.sin(np.arange(4410) * 0.08) * 10000).astype(np.int16)
            with wave.open(path, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(44100)
                output.writeframes(samples.tobytes())
            cls = env["QueueAudioTrack"]
            chunks = await asyncio.get_running_loop().run_in_executor(None, cls.decode_file, path)
            track = cls()
            try:
                self.assertAlmostEqual(track.enqueue(chunks), 0.1, places=2)
                first, second = await track.recv(), await track.recv()
                self.assertEqual(first.samples, 960)
                self.assertEqual(second.pts - first.pts, 960)
                self.assertTrue(np.any(first.to_ndarray()))
            finally:
                track.stop()

    async def test_cancelled_walk_returns_to_zero(self):
        env = board_parts()
        messages = []
        class Publisher:
            def publish_without_callback(self, topic, message):
                messages.append(message)
        task = asyncio.create_task(env["_joystick_walk"](Publisher(), {"ly": 0.5}, 2))
        await asyncio.sleep(0.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(messages[-1]["ly"], 0)

    async def test_invalid_recording_rejected_before_recognition(self):
        env = board_parts()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "wrong.wav")
            with wave.open(path, "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48000)
                output.writeframes(b"\0" * 400)
            with self.assertRaisesRegex(ValueError, "16000Hz"):
                env["transcribe_wav"](path)


if __name__ == "__main__":
    unittest.main()
