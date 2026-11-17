"""Startup boundaries; these tests never contact a robot or load real models."""
import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "voice_control_board.py"


def load_entry():
    spec = importlib.util.spec_from_file_location("board_entry_test", str(ENTRY))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportBoundaryTest(unittest.TestCase):
    def test_importing_entry_does_not_import_speech_or_communication(self):
        # A fresh interpreter catches real top-level imports, including indirect ones.
        script = """
import importlib.abc, runpy, sys
class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'sherpa_onnx', 'aiortc', 'unitree_webrtc_connect',
            'voice_control_board_runtime', 'numpy', 'av'
        }:
            raise ImportError('loaded before connection: ' + fullname)
sys.meta_path.insert(0, BlockRuntime())
runpy.run_path(sys.argv[1], run_name='board_import_check')
"""
        result = subprocess.run([sys.executable, "-c", script, str(ENTRY)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_loss_cancels_control_and_propagates_failure(self):
        entry = load_entry()
        conn = SimpleNamespace(pc=SimpleNamespace(connectionState="connected"))
        entered = asyncio.Event()
        cleaned = []
        async def control():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)
        runner = asyncio.create_task(entry.run_connected(conn, control(), poll_interval=0.01))
        await entered.wait()
        conn.pc.connectionState = "failed"
        with self.assertRaisesRegex(ConnectionError, "failed"):
            await asyncio.wait_for(runner, 1)
        self.assertEqual(cleaned, [True])

    async def test_control_exit_leaves_no_connection_monitor(self):
        entry = load_entry()
        conn = SimpleNamespace(pc=SimpleNamespace(connectionState="connected"))
        before = set(asyncio.all_tasks())
        self.assertEqual(await entry.run_connected(conn, asyncio.sleep(0, result=7)), 7)
        self.assertEqual(set(asyncio.all_tasks()), before)

    async def test_closed_datachannel_ends_control_even_if_peer_is_connected(self):
        entry = load_entry()
        conn = SimpleNamespace(pc=SimpleNamespace(connectionState="connected"),
                               datachannel=SimpleNamespace(channel=SimpleNamespace(readyState="closed")))
        with self.assertRaisesRegex(ConnectionError, "数据通道"):
            await entry.run_connected(conn, asyncio.sleep(60))

    async def test_service_sigterm_returns_143_after_application_cleanup(self):
        import signal
        entry = load_entry()
        handlers = {}
        entered = asyncio.Event()
        cleaned = []
        async def application(argv=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.append(True)
        loop = asyncio.get_running_loop()
        def add_handler(number, callback, *args):
            handlers[number] = lambda: callback(*args)
        with patch.object(entry, "main", application):
            with patch.object(loop, "add_signal_handler", side_effect=add_handler):
                with patch.object(loop, "remove_signal_handler"):
                    runner = asyncio.create_task(entry.run_with_signals())
                    await entered.wait()
                    handlers[signal.SIGTERM]()
                    self.assertEqual(await runner, 143)
        self.assertEqual(cleaned, [True])

    async def test_one_stop_signal_cleans_main_before_cancelling_background_tasks(self):
        entry = load_entry()
        handlers = {}
        started = asyncio.Event()
        events = []
        background = None
        async def read_audio():
            try:
                await asyncio.Event().wait()
            finally:
                events.append("audio-stopped")
        async def application(argv=None):
            nonlocal background
            background = asyncio.create_task(read_audio())
            await asyncio.sleep(0)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.assertFalse(background.done())
                events.append("cleanup-start")
                # 再次按下Ctrl+C不能打断第一次的清理。
                handlers[__import__("signal").SIGINT]()
                await asyncio.sleep(0)
                self.assertFalse(background.done())
                background.cancel()
                await asyncio.gather(background, return_exceptions=True)
                events.append("cleanup-complete")
        loop = asyncio.get_running_loop()
        def add_handler(number, callback, *args):
            handlers[number] = lambda: callback(*args)
        with patch.object(entry, "main", application):
            with patch.object(loop, "add_signal_handler", side_effect=add_handler):
                with patch.object(loop, "remove_signal_handler"):
                    runner = asyncio.create_task(entry.run_with_signals())
                    await started.wait()
                    handlers[__import__("signal").SIGINT]()
                    self.assertEqual(await asyncio.wait_for(runner, 1), 130)
        self.assertEqual(events, ["cleanup-start", "audio-stopped", "cleanup-complete"])

    async def test_failed_aiortc19_handshake_is_drained_before_close(self):
        # Execute the actual board wheel's __connect(), rather than a lookalike.
        import ast
        wheel = next((ROOT.parent / "board_wheels").glob("aiortc-1.9.0-*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            tree = ast.parse(archive.read("aiortc/rtcpeerconnection.py"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "RTCPeerConnection")
        cls.body = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "__connect"]
        cls.bases = []
        env = {"__name__": "aiortc.rtcpeerconnection"}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "aiortc19-connect", "exec"), env)
        pc = env["RTCPeerConnection"]()
        started = asyncio.Event()
        drained = []
        class Ice:
            iceGatherer = SimpleNamespace(getLocalCandidates=lambda: [True])
            async def start(self, parameters):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    drained.append(True)
        transceiver = SimpleNamespace(_transport=SimpleNamespace(transport=Ice()))
        # SimpleNamespace is unhashable, so preserve identity with a custom map.
        class RemoteIce:
            def __contains__(self, key):
                return key is transceiver
            def __getitem__(self, key):
                return None
        pc._RTCPeerConnection__transceivers = [transceiver]
        pc._RTCPeerConnection__remoteIce = RemoteIce()
        pc._RTCPeerConnection__sctp = None
        first = asyncio.create_task(pc._RTCPeerConnection__connect())
        second = asyncio.create_task(pc._RTCPeerConnection__connect())
        await started.wait()
        await asyncio.sleep(0)
        async def close():
            self.assertEqual(len(drained), 2)
        conn = SimpleNamespace(pc=pc, disconnect=close)
        await load_entry().disconnect(conn)
        self.assertTrue(first.cancelled())
        self.assertTrue(second.cancelled())

    async def run_mode(self, flag, fail=None):
        entry = load_entry()
        events = []
        main_thread = threading.get_ident()
        ticks = []

        class Connection:
            def __init__(self, method, ip):
                self.pc = SimpleNamespace(connectionState="new")

            async def connect(self):
                events.append("connect")
                if fail == "connect":
                    raise RuntimeError("handshake failed")
                self.pc.connectionState = "connected"

            async def disconnect(self):
                events.append("disconnect")
                self.pc.connectionState = "closed"

        def load(name):
            self.assertNotEqual(threading.get_ident(), main_thread)
            events.append(name)
            time.sleep(0.025)
            if fail == name:
                raise RuntimeError("model missing")

        runtime = SimpleNamespace(
            load_asr=lambda: load("asr"), load_tts=lambda: load("tts"),
            run_control=AsyncMock(), run_offline=AsyncMock())

        def import_runtime():
            self.assertNotEqual(threading.get_ident(), main_thread)
            events.append("import-runtime")
            time.sleep(0.025)
            return runtime

        driver = SimpleNamespace(UnitreeWebRTCConnection=Connection,
                                 WebRTCConnectionMethod=SimpleNamespace(LocalSTA="local"))
        async def tick():
            for _ in range(12):
                await asyncio.sleep(0.01)
                ticks.append(True)

        with patch.dict(sys.modules, {"unitree_webrtc_connect.webrtc_driver": driver}):
            with patch.object(entry, "import_runtime", import_runtime):
                code, _ = await asyncio.gather(entry.main([flag]), tick())
        return code, events, runtime, ticks

    async def test_startup_connects_before_import_and_models_without_motion(self):
        code, events, runtime, ticks = await self.run_mode("--check-startup")
        self.assertEqual(code, 0)
        self.assertEqual(events, ["connect", "import-runtime", "asr", "tts", "disconnect"])
        self.assertTrue(ticks)
        runtime.run_control.assert_not_awaited()

    async def test_connection_check_never_imports_runtime(self):
        code, events, runtime, _ = await self.run_mode("--check-connection")
        self.assertEqual(code, 0)
        self.assertEqual(events, ["connect", "disconnect"])
        runtime.run_control.assert_not_awaited()

    async def test_failed_handshake_does_not_import_models_or_retry_same_process(self):
        code, events, runtime, _ = await self.run_mode("--check-startup", fail="connect")
        self.assertEqual(code, 1)
        self.assertEqual(events, ["connect", "disconnect"])
        runtime.run_control.assert_not_awaited()

    async def test_model_failure_disconnects_and_returns_error(self):
        code, events, runtime, _ = await self.run_mode("--check-startup", fail="tts")
        self.assertEqual(code, 1)
        self.assertEqual(events[-1], "disconnect")
        runtime.run_control.assert_not_awaited()

    async def test_model_connection_check_uses_safe_startup(self):
        code, events, runtime, _ = await self.run_mode("--check-connection-with-models")
        self.assertEqual(code, 0)
        self.assertEqual(events, ["connect", "import-runtime", "asr", "tts", "disconnect"])
        runtime.run_control.assert_not_awaited()

    async def test_offline_modes_never_construct_connection(self):
        entry = load_entry()
        runtime = SimpleNamespace(run_offline=AsyncMock())
        for flag in ("--self-test", "--check-models", "--transcribe-wav"):
            with self.subTest(flag=flag):
                arguments = [flag, "fixture.wav"] if flag == "--transcribe-wav" else [flag]
                with patch.object(entry, "import_runtime", return_value=runtime):
                    self.assertEqual(await entry.main(arguments), 0)
        self.assertEqual(runtime.run_offline.await_count, 3)


if __name__ == "__main__":
    unittest.main()
