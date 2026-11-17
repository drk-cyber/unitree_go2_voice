"""CLI wiring and validation without robot, microphone or native speech models."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def load_entry():
    path = Path(__file__).with_name("voice_control_board.py")
    spec = importlib.util.spec_from_file_location("board_cli_test", str(path))
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


class CliTests(unittest.IsolatedAsyncioTestCase):
    async def test_listen_only_never_constructs_connection_or_loads_tts(self):
        entry = load_entry()
        runtime = SimpleNamespace(run_listen_only=AsyncMock(), load_tts=Mock())
        driver = SimpleNamespace(UnitreeWebRTCConnection=Mock())
        with patch.dict(sys.modules, {"unitree_webrtc_connect.webrtc_driver": driver}):
            with patch.object(entry, "import_runtime", return_value=runtime):
                code = await entry.main(["--listen-only", "--hands-free", "--wake-word", "小宇",
                                         "--mic-device", "plughw:3,0", "--vad-threshold", "750"])
        self.assertEqual(code, 0)
        driver.UnitreeWebRTCConnection.assert_not_called()
        runtime.load_tts.assert_not_called()
        runtime.run_listen_only.assert_awaited_once()
        args = runtime.run_listen_only.await_args.args[0]
        self.assertEqual(args.wake_word, "小宇")
        self.assertEqual(args.mic_device, "plughw:3,0")
        self.assertEqual(args.vad_threshold, 750.0)

    async def test_invalid_cli_values_fail_before_importing_runtime(self):
        for option, value in (("--volume", "11"), ("--volume", "-1"),
                              ("--volume", "8.5"), ("--wake-word", "狗"),
                              ("--wake-word", " 。 ！"), ("--vad-threshold", "nan"),
                              ("--vad-threshold", "inf"), ("--vad-threshold", "0"),
                              ("--vad-threshold", "30001")):
            with self.subTest(option=option, value=value):
                entry = load_entry()
                with patch.object(entry, "import_runtime") as import_runtime:
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as result:
                            await entry.main(["--listen-only", option, value])
                    self.assertEqual(result.exception.code, 2)
                    import_runtime.assert_not_called()

    async def test_listen_only_failure_returns_nonzero(self):
        entry = load_entry()
        runtime = SimpleNamespace(run_listen_only=AsyncMock(side_effect=RuntimeError("no microphone")))
        with patch.object(entry, "import_runtime", return_value=runtime):
            self.assertEqual(await entry.main(["--listen-only"]), 1)

    async def test_runtime_receives_volume_and_hands_free_options(self):
        entry = load_entry()
        conn = SimpleNamespace(pc=SimpleNamespace(connectionState="connected"),
                               connect=AsyncMock(), disconnect=AsyncMock())
        factory = Mock(return_value=conn)
        driver = SimpleNamespace(UnitreeWebRTCConnection=factory,
                                 WebRTCConnectionMethod=SimpleNamespace(LocalSTA="local"))
        runtime = SimpleNamespace(load_asr=Mock(), load_tts=Mock(), run_control=AsyncMock())
        with patch.dict(sys.modules, {"unitree_webrtc_connect.webrtc_driver": driver}):
            with patch.object(entry, "import_runtime", return_value=runtime):
                code = await entry.main(["--hands-free", "--volume", "10", "--wake-word", "小 宇！",
                                         "--mic-device", "plughw:3,0", "--vad-threshold", "600"])
        self.assertEqual(code, 0)
        runtime.run_control.assert_awaited_once_with(
            conn, volume=10, hands_free=True, wake_word="小 宇！",
            mic_device="plughw:3,0", vad_threshold=600.0)
        conn.disconnect.assert_awaited_once()

    async def test_defaults_keep_manual_mode_and_volume_eight(self):
        entry = load_entry()
        conn = SimpleNamespace(pc=SimpleNamespace(connectionState="connected"),
                               connect=AsyncMock(), disconnect=AsyncMock())
        driver = SimpleNamespace(UnitreeWebRTCConnection=Mock(return_value=conn),
                                 WebRTCConnectionMethod=SimpleNamespace(LocalSTA="local"))
        runtime = SimpleNamespace(load_asr=Mock(), load_tts=Mock(), run_control=AsyncMock())
        with patch.dict(sys.modules, {"unitree_webrtc_connect.webrtc_driver": driver}):
            with patch.object(entry, "import_runtime", return_value=runtime):
                self.assertEqual(await entry.main([]), 0)
        runtime.run_control.assert_awaited_once_with(
            conn, volume=8, hands_free=False, wake_word="小狗",
            mic_device="plughw:CARD=Device,DEV=0", vad_threshold=500.0)


if __name__ == "__main__":
    unittest.main()
