"""Local regression checks; never connects to or moves a robot."""
import asyncio
import ast
import inspect
import unittest
from pathlib import Path

import voice_control as voice


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_audio_decodes_and_streams(self):
        files = list(Path(__file__).parent.glob("tts_cache_*.audio"))
        if not files:
            self.skipTest("No cached audio fixture")
        chunks = await asyncio.to_thread(voice.QueueAudioTrack.decode_file, str(files[0]))
        self.assertGreater(len(chunks), 0)
        track = voice.QueueAudioTrack()
        try:
            duration = track.enqueue(chunks)
            self.assertGreater(duration, 0)
            first = await track.recv()
            second = await track.recv()
            self.assertEqual(first.samples, 960)
            self.assertEqual(first.sample_rate, 48000)
            self.assertEqual(second.pts - first.pts, 960)
        finally:
            track.stop()

    async def test_cancelled_walk_sends_zero(self):
        class Publisher:
            def __init__(self):
                self.messages = []

            def publish_without_callback(self, topic, message):
                self.messages.append(message)

        pub = Publisher()
        task = asyncio.create_task(voice._joystick_walk(pub, {"ly": 0.5}, 2))
        await asyncio.sleep(0.03)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(pub.messages[-1]["ly"], 0)

    async def test_main_shares_walk_task_with_stop(self):
        tree = ast.parse(inspect.getsource(voice.main))
        globals_used = {name for node in ast.walk(tree)
                        if isinstance(node, ast.Global) for name in node.names}
        self.assertIn("_walk_task", globals_used)


if __name__ == "__main__":
    unittest.main()
