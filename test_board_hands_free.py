"""Wake gating, real PCM segmentation and capture lifecycle, without a robot."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import numpy as np


def runtime():
    path = Path(__file__).with_name("voice_control_board_runtime.py")
    spec = importlib.util.spec_from_file_location("hands_free_runtime_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"sherpa_onnx": SimpleNamespace()}):
        spec.loader.exec_module(mod)
    return mod


class WakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = runtime()

    def test_no_motion_without_wake_or_from_conversation(self):
        gate = self.mod.WakeGate("小狗")
        for text in ("前进", "我说小狗前进", "先不要小狗前进", "狗在前进"):
            self.assertEqual(gate.accept(text, 1), ("ignore", None))
        for text in ("小狗不要前进", "小狗前进然后右转", "小狗前进了吗", "小狗不停地前进"):
            self.assertEqual(gate.accept(text, 1), ("unknown", None))

    def test_same_sentence_punctuation_and_single_use(self):
        gate = self.mod.WakeGate("小狗")
        event, command = gate.accept("小狗，请前进一下。", 10)
        self.assertEqual(event, "command")
        self.assertEqual(command[0], "walk")
        self.assertEqual(command[1][0], {"ly": 0.5})
        self.assertEqual(gate.accept("右转", 11), ("ignore", None))

    def test_wake_then_command_expiry_and_rearm_after_reply(self):
        gate = self.mod.WakeGate("小狗")
        self.assertEqual(gate.accept("小狗！", 1), ("wake", None))
        gate.arm(4)  # 模拟唤醒播报用了3秒，窗口从播报后开始。
        self.assertEqual(gate.accept("比心", 11)[0], "command")
        self.assertEqual(gate.accept("比心", 11.1)[0], "ignore")
        gate.accept("小狗", 20)
        self.assertEqual(gate.accept("前进", 29)[0], "ignore")

    def test_only_complete_stop_phrase_bypasses_wake(self):
        gate = self.mod.WakeGate("小狗")
        for text in ("停", "停止！", "暂停。", "请停下"):
            self.assertEqual(gate.accept(text, 1), ("command", ("stop", None)))
        for text in ("不停", "不要停", "暂停播放", "停下来了吗"):
            self.assertEqual(gate.accept(text, 1), ("ignore", None))

    def test_custom_wake_word(self):
        gate = self.mod.WakeGate("旺财")
        self.assertEqual(gate.accept("小狗前进", 1)[0], "ignore")
        self.assertEqual(gate.accept("旺财右转", 1)[0], "command")
        self.assertEqual(self.mod.WakeGate("【小狗】").accept("小狗，比心", 1)[0], "command")

    def test_collection_reply_requires_a_complete_command(self):
        gate = self.mod.WakeGate("小狗")
        reply = ("reply", "好的，我去执行任务")
        self.assertEqual(gate.accept("回收医疗废物", 1), ("ignore", None))
        self.assertEqual(gate.accept("小狗，不要回收医疗废物。", 2), ("unknown", None))
        self.assertEqual(gate.accept("小狗，请回收医疗废物。", 3), ("command", reply))
        self.assertEqual(gate.accept("小狗", 4), ("wake", None))
        self.assertEqual(gate.accept("收集医疗垃圾", 5), ("command", reply))
        self.assertEqual(gate.accept("回收医疗费物", 6), ("ignore", None))
        self.assertEqual(gate.accept("小狗，不要回收医疗费物。", 7), ("unknown", None))
        self.assertEqual(gate.accept("小狗。", 8), ("wake", None))
        self.assertEqual(gate.accept("请回收医疗费物。", 14), ("command", reply))
        self.assertEqual(self.mod.match_command("请回收医疗废物。"), reply)
        self.assertEqual(self.mod.match_command("请回收医疗费物。"), reply)
        self.assertIsNone(self.mod.match_command("不要回收医疗废物。"))


class SegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = runtime()
        cls.quiet = bytes(640)
        cls.speech = np.full(320, 3000, dtype="<i2").tobytes()

    def test_speech_endpoint_includes_pre_roll_and_last_syllable(self):
        segmenter = self.mod.SpeechSegmenter(500)
        for _ in range(12):
            self.assertIsNone(segmenter.feed(self.quiet))
        for _ in range(25):
            self.assertIsNone(segmenter.feed(self.speech))
        for _ in range(29):
            self.assertIsNone(segmenter.feed(self.quiet))
        utterance = segmenter.feed(self.quiet)
        self.assertEqual(utterance, self.quiet * 10 + self.speech * 25 + self.quiet * 30)

    def test_silence_click_and_overlong_audio_cannot_emit_command(self):
        segmenter = self.mod.SpeechSegmenter(500)
        for frame in [self.quiet] * 50 + [self.speech] * 3 + [self.quiet] * 40:
            self.assertIsNone(segmenter.feed(frame))
        # 连续长语音不能在5秒处分成潜在指令；整句丢弃到尾部静音。
        for frame in [self.speech] * 300 + [self.quiet] * 30:
            self.assertIsNone(segmenter.feed(frame))
        results = [segmenter.feed(frame) for frame in [self.speech] * 12 + [self.quiet] * 30]
        self.assertEqual(sum(item is not None for item in results), 1)


class MicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mod = runtime()
        self.speech = np.full(320, 3000, dtype="<i2").tobytes()
        self.quiet = bytes(640)

    def fake_process(self, background=None):
        stdout = asyncio.StreamReader()
        stdout.feed_data((background if background is not None else bytes(640)) * 50)
        stderr = asyncio.StreamReader()
        closed = asyncio.Event()
        process = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=None)
        def terminate():
            process.returncode = -15
            stdout.feed_eof()
            stderr.feed_eof()
            closed.set()
        process.terminate = Mock(side_effect=terminate)
        process.kill = Mock(side_effect=terminate)
        async def wait():
            await closed.wait()
            return process.returncode
        process.wait = wait
        return process, terminate

    async def test_steady_background_does_not_block_listening_forever(self):
        noise = np.full(320, 800, dtype="<i2").tobytes()
        process, _ = self.fake_process(background=noise)
        # 500 固定阈值下，噪声800使旧版永远进不了“安静”状态。
        with patch.object(self.mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            async with self.mod.ContinuousMicrophone("noisy-room", 500) as mic:
                process.stdout.feed_data(noise * 70 + self.speech * 20 + noise * 30)
                pcm = await asyncio.wait_for(mic.next_utterance(), 0.5)
                self.assertTrue(pcm)

    async def test_stream_start_failure_switches_to_proven_file_capture(self):
        closed = []
        class BrokenStream:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                closed.append(True)
            async def next_utterance(self):
                raise self.mod.CaptureUnavailable("no streaming bytes")
        stream = BrokenStream()
        stream.mod = self.mod
        on_event = AsyncMock(return_value=False)
        async def fallback(device, word, threshold, callback, show_levels=False):
            self.assertEqual(closed, [True])
            self.assertEqual(device, "test-mic")
            self.assertIs(callback, on_event)
        with patch.object(self.mod, "ContinuousMicrophone", return_value=stream):
            with patch.object(self.mod, "listen_file_chunks", AsyncMock(side_effect=fallback)) as capture:
                await self.mod.listen_hands_free("test-mic", "小狗", 500, on_event, show_levels=True)
        capture.assert_awaited_once()

    async def test_file_capture_wakes_and_never_uses_substring_motion(self):
        events = []
        async def on_event(event, command):
            events.append((event, command))
            return False
        class EndTest(Exception):
            pass
        # “没有唤醒的前进”不执行；已唤醒的完整指令会执行；取消录音任务应退出。
        with patch.object(self.mod, "record_and_transcribe",
                          side_effect=["前进", "小狗比心", EndTest("done")]):
            with self.assertRaises(EndTest):
                await self.mod.listen_file_chunks("test-mic", "小狗", 500, on_event)
        self.assertEqual(events[0], ("ignore", None))
        self.assertEqual(events[1][0], "command")
        self.assertEqual(events[1][1][1][0], "FingerHeart")

    async def test_wav_capture_uses_proven_duration_and_surfaces_alsa_error(self):
        with patch.object(self.mod.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as capture:
            with patch.object(self.mod, "transcribe_wav", return_value="小狗打招呼") as transcribe:
                self.assertEqual(self.mod.record_and_transcribe("test-mic"), "小狗打招呼")
        command = capture.call_args.args[0]
        self.assertEqual(command[command.index("-d") + 1], "3")
        self.assertEqual(command[-1], self.mod.REC_WAV)
        transcribe.assert_called_once_with(self.mod.REC_WAV)
        with patch.object(self.mod.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stderr=b"audio open error: Device or resource busy")):
            with self.assertRaisesRegex(RuntimeError, "Device or resource busy"):
                self.mod.record_and_transcribe("test-mic")

    async def test_wav_timeout_preserves_recording_details_without_running_asr(self):
        error = self.mod.subprocess.TimeoutExpired(
            ["arecord"], 10, stderr=b"Recording WAVE: Signed 16 bit Little Endian, Rate 16000 Hz, Mono")
        with patch.object(self.mod.subprocess, "run", side_effect=error):
            with patch.object(self.mod, "transcribe_wav") as transcribe:
                with self.assertRaisesRegex(RuntimeError, "Recording WAVE"):
                    self.mod.record_and_transcribe("test-mic")
        transcribe.assert_not_called()

    async def test_audio_during_speech_reply_is_discarded_and_process_reaped(self):
        process, _ = self.fake_process()
        sentence = self.quiet * 15 + self.speech * 20 + self.quiet * 30
        with patch.object(self.mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            async with self.mod.ContinuousMicrophone("test-device", 500) as mic:
                process.stdout.feed_data(sentence)
                first = await asyncio.wait_for(mic.next_utterance(), 1)
                self.assertTrue(first)
                self.assertFalse(mic.enabled)
                # 读到一句后自动暂停；回复声在 pipe 中被读走但不进队列。
                process.stdout.feed_data(sentence * 2)
                await asyncio.sleep(0.02)
                self.assertTrue(mic.queue.empty())
                mic.resume()
                self.assertTrue(mic.queue.empty())
                process.stdout.feed_data(sentence)
                self.assertTrue(await asyncio.wait_for(mic.next_utterance(), 1))
        process.terminate.assert_called_once()
        self.assertTrue(mic.reader.done())
        self.assertTrue(mic.errors.done())

    async def test_resume_requires_quiet_before_accepting_new_sentence(self):
        process, _ = self.fake_process()
        with patch.object(self.mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            async with self.mod.ContinuousMicrophone("test-device", 500) as mic:
                # 恢复时尚未播完的尾音不能作为一句指令。
                process.stdout.feed_data(self.speech * 20 + self.quiet * 30)
                await asyncio.sleep(0.02)
                self.assertTrue(mic.queue.empty())
                process.stdout.feed_data(self.speech * 20 + self.quiet * 30)
                self.assertTrue(await asyncio.wait_for(mic.next_utterance(), 1))

    async def test_arecord_failure_reports_device_error_instead_of_hanging(self):
        process, end = self.fake_process()
        with patch.object(self.mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
            async with self.mod.ContinuousMicrophone("missing", 500) as mic:
                process.stderr.feed_data(b"arecord: audio open error: No such file")
                end()
                with self.assertRaisesRegex(RuntimeError, "audio open error"):
                    await asyncio.wait_for(mic.next_utterance(), 1)

    async def test_real_subprocess_with_full_pipe_is_reaped_on_exit(self):
        # 真实子进程模拟持续录音，覆盖满stdout管道后的退出，而非仅替身。
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys,time; sys.stdout.buffer.write(bytes(2000000)); sys.stdout.buffer.flush(); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await asyncio.sleep(0.1)
            with patch.object(self.mod.asyncio, "create_subprocess_exec", AsyncMock(return_value=process)):
                mic = await self.mod.ContinuousMicrophone("pipe-test", 500).__aenter__()
                await asyncio.wait_for(mic.__aexit__(None, None, None), 4)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.returncode is None:
                process.kill()
                await process.stdout.read()
                await process.wait()

    async def test_read_only_listen_does_not_load_tts_or_control_robot(self):
        args = SimpleNamespace(mic_device="mic", wake_word="小狗", vad_threshold=500)
        with patch.object(self.mod, "load_asr") as asr, patch.object(self.mod, "load_tts") as tts:
            with patch.object(self.mod, "listen_hands_free", AsyncMock()) as listen:
                await self.mod.run_listen_only(args)
        asr.assert_called_once()
        tts.assert_not_called()
        listen.assert_awaited_once()

    async def test_runtime_passes_volume_and_keeps_manual_path(self):
        pub = SimpleNamespace(publish_request_new=AsyncMock(return_value={"data": {"data": '{"name":"normal"}'}}),
                              publish_without_callback=Mock())
        conn = SimpleNamespace(datachannel=SimpleNamespace(pub_sub=pub), pc=SimpleNamespace(addTrack=Mock()))
        with patch.object(self.mod, "wait_for_enter", AsyncMock(side_effect=EOFError)):
            await self.mod.run_control(conn, volume=10)
        volume_calls = [call for call in pub.publish_request_new.await_args_list
                        if call.args[1].get("api_id") == 1003]
        self.assertEqual(len(volume_calls), 1)
        self.assertEqual(volume_calls[0].args[1]["parameter"]["volume"], 10)
        self.assertIsNone(self.mod.AUDIO_TRACK)

    async def test_hands_free_ignores_chat_and_executes_woken_action(self):
        pub = SimpleNamespace(publish_request_new=AsyncMock(return_value={
            "data": {"data": '{"name":"normal"}', "header": {"status": {"code": 0}}}}),
            publish_without_callback=Mock())
        conn = SimpleNamespace(datachannel=SimpleNamespace(pub_sub=pub), pc=SimpleNamespace(addTrack=Mock()))
        spoken = AsyncMock()
        async def listen(device, word, threshold, on_event):
            gate = self.mod.WakeGate(word)
            self.assertFalse(await on_event(*gate.accept("前进", 1)))
            self.assertEqual(pub.publish_request_new.await_count, 2)  # 仅查询模式和设置音量
            self.assertTrue(await on_event(*gate.accept("小狗比心", 2)))
        with patch.object(self.mod, "listen_hands_free", listen), patch.object(self.mod, "dog_say", spoken):
            await self.mod.run_control(conn, volume=9, hands_free=True)
        action = pub.publish_request_new.await_args.args[1]["api_id"]
        self.assertEqual(action, self.mod.SPORT_CMD["FingerHeart"])
        spoken.assert_awaited_once_with(conn, "给你比个心")

    async def test_collection_command_replies_without_sending_motion(self):
        pub = SimpleNamespace(publish_request_new=AsyncMock(return_value={
            "data": {"data": '{"name":"normal"}'}}), publish_without_callback=Mock())
        conn = SimpleNamespace(datachannel=SimpleNamespace(pub_sub=pub), pc=SimpleNamespace(addTrack=Mock()))
        spoken = AsyncMock()

        async def listen(device, word, threshold, on_event):
            command = self.mod.WakeGate(word).accept("小狗，请回收医疗废物。", 1)
            self.assertTrue(await on_event(*command))

        with patch.object(self.mod, "listen_hands_free", listen), patch.object(self.mod, "dog_say", spoken):
            await self.mod.run_control(conn, hands_free=True)

        spoken.assert_awaited_once_with(conn, "好的，我去执行任务")
        self.assertEqual([call.args[0] for call in pub.publish_request_new.await_args_list],
                         [self.mod.RTC_TOPIC["MOTION_SWITCHER"], self.mod.RTC_TOPIC["VUI"]])
        pub.publish_without_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
