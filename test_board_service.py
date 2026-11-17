"""Service installation checks; never invoke systemctl or contact hardware."""
import ast
import configparser
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "board_service_installer_test", str(ROOT / "install_voice_service.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServiceInstallerTests(unittest.TestCase):
    def test_check_has_no_install_or_start_side_effects(self):
        installer = load_installer()
        with patch.object(installer, "preflight") as preflight:
            with patch.object(installer, "run") as run:
                with patch.object(installer.shutil, "copyfile") as copy:
                    self.assertEqual(installer.main(["--check"]), 0)
        preflight.assert_called_once_with()
        run.assert_not_called()
        copy.assert_not_called()

    def test_enable_rejects_nonroot_before_preflight_or_commands(self):
        installer = load_installer()
        with patch.object(installer.os, "geteuid", return_value=1000, create=True):
            with patch.object(installer, "preflight") as preflight:
                with patch.object(installer, "run") as run:
                    with self.assertRaisesRegex(RuntimeError, "sudo"):
                        installer.main(["--enable"])
        preflight.assert_not_called()
        run.assert_not_called()

    def install_in_temp(self, existing=None, active=False):
        installer = load_installer()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        source_dir = folder / "source"
        destination_dir = folder / "systemd"
        source_dir.mkdir()
        destination_dir.mkdir()
        unit_text = (ROOT / "go2-voice.service").read_bytes()
        (source_dir / installer.SERVICE).write_bytes(unit_text)
        destination = destination_dir / installer.SERVICE
        if existing is not None:
            destination.write_bytes(existing)
        commands = []
        def run(command):
            commands.append(command)
            return ""
        status = Mock(return_value=SimpleNamespace(returncode=0 if active else 3))
        with patch.object(installer, "ROOT", source_dir):
            with patch.object(installer, "DESTINATION", destination):
                with patch.object(installer.os, "geteuid", return_value=0, create=True):
                    with patch.object(installer, "preflight"):
                        with patch.object(installer, "run", side_effect=run):
                            with patch.object(installer.subprocess, "run", status):
                                with contextlib.redirect_stdout(io.StringIO()) as output:
                                    result = installer.main(["--enable"])
        return result, commands, status, destination, unit_text, output.getvalue()

    def test_new_install_enables_and_starts_reviewed_unit(self):
        result, commands, status, destination, expected, output = self.install_in_temp()
        self.assertEqual(result, 0)
        self.assertEqual(destination.read_bytes(), expected)
        self.assertEqual(commands, [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", "go2-voice.service"],
            ["systemctl", "start", "go2-voice.service"],
        ])
        status.assert_called_once_with(
            ["systemctl", "is-active", "--quiet", "go2-voice.service"])
        self.assertIn("可能", output)
        self.assertIn("日志", output)

    def test_changed_active_unit_is_backed_up_and_stopped_without_fast_reconnect(self):
        previous = b"[Service]\nExecStart=/old/voice.py\n"
        result, commands, _, destination, expected, output = self.install_in_temp(
            existing=previous, active=True)
        self.assertEqual(result, 0)
        self.assertEqual(destination.read_bytes(), expected)
        backups = list(destination.parent.glob("go2-voice.service.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), previous)
        self.assertIn(["systemctl", "stop", "go2-voice.service"], commands)
        self.assertNotIn(["systemctl", "start", "go2-voice.service"], commands)
        self.assertIn("90秒后", output)

    def test_reinstalling_same_unit_does_not_restart_active_connection(self):
        same = (ROOT / "go2-voice.service").read_bytes()
        _, commands, status, destination, _, _ = self.install_in_temp(existing=same, active=True)
        self.assertNotIn(["systemctl", "stop", "go2-voice.service"], commands)
        self.assertNotIn(["systemctl", "restart", "go2-voice.service"], commands)
        self.assertEqual(list(destination.parent.glob("*.backup-*")), [])
        # systemctl start is idempotent for an already running service.
        self.assertIn(["systemctl", "start", "go2-voice.service"], commands)
        status.assert_not_called()

    def preflight_in_temp(self, as_root=False, cards=None):
        installer = load_installer()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        for name in ("voice_control_board.py", "voice_control_board_runtime.py", installer.SERVICE):
            (folder / name).write_text("fixture", encoding="utf-8")
        for directory in ("models/sensevoice", "models/melo_tts"):
            (folder / directory).mkdir(parents=True)
        original_is_dir = Path.is_dir
        original_read_text = Path.read_text
        systemd_marker = Path("/run/systemd/system")
        cards_file = Path("/proc/asound/cards")
        if cards is None:
            cards = " 0 [Device         ]: USB-Audio - Usb Audio Device\n"
        def is_dir(path):
            return True if path == systemd_marker else original_is_dir(path)
        def read_text(path, *args, **kwargs):
            return cards if path == cards_file else original_read_text(path, *args, **kwargs)
        commands = Mock(return_value="")
        account = SimpleNamespace(pw_uid=1000)
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account))
        fake_grp = SimpleNamespace(getgrnam=Mock())
        with contextlib.ExitStack() as patches:
            patches.enter_context(patch.object(installer, "ROOT", folder))
            patches.enter_context(patch.object(installer, "__file__", str(folder / "install_voice_service.py")))
            patches.enter_context(patch.object(installer.sys, "platform", "linux"))
            patches.enter_context(patch.object(installer.os, "geteuid", return_value=0 if as_root else 1000,
                                              create=True))
            patches.enter_context(patch.dict("sys.modules", {"pwd": fake_pwd, "grp": fake_grp}))
            patches.enter_context(patch.object(Path, "is_dir", is_dir))
            patches.enter_context(patch.object(Path, "read_text", read_text))
            patches.enter_context(patch.object(installer.shutil, "which", return_value="/usr/bin/arecord"))
            patches.enter_context(patch.object(installer, "run", commands))
            with contextlib.redirect_stdout(io.StringIO()):
                installer.preflight()
        return commands.call_args_list

    def test_preflight_checks_service_interpreter_without_importing_models_or_robot(self):
        calls = self.preflight_in_temp()
        self.assertEqual(len(calls), 2)
        interpreter_probe = calls[0].args[0]
        self.assertEqual(interpreter_probe[:2], ["/usr/bin/python3", "-c"])
        self.assertIn("find_spec", interpreter_probe[2])
        self.assertNotIn("load_asr", interpreter_probe[2])
        self.assertNotIn("UnitreeWebRTCConnection", interpreter_probe[2])
        self.assertEqual(calls[1].args[0][:2], ["systemd-analyze", "verify"])

    def test_root_preflight_checks_packages_as_unitree_with_correct_home(self):
        probe = self.preflight_in_temp(as_root=True)[0].args[0]
        self.assertEqual(probe[:8], [
            "runuser", "-u", "unitree", "--", "env", "HOME=/home/unitree", "/usr/bin/python3", "-c"])

    def test_preflight_rejects_jetson_only_audio_before_service_install(self):
        with self.assertRaisesRegex(RuntimeError, "USB"):
            self.preflight_in_temp(cards=" 2 [APE ]: tegra-hda - NVIDIA Jetson Orin NX APE\n")


class ServiceUnitTests(unittest.TestCase):
    def test_unit_uses_ordinary_user_named_usb_and_bounded_fresh_process_recovery(self):
        # systemd legitimately allows multiple Environment entries.
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(ROOT / "go2-voice.service", encoding="utf-8")
        service = config["Service"]
        self.assertEqual(service["User"], "unitree")
        self.assertIn("audio", service["SupplementaryGroups"].split())
        self.assertEqual(service["WorkingDirectory"], "/home/unitree/go2_voice")
        self.assertEqual(service["ExecStart"].split()[:3],
                         ["/usr/bin/python3", "-u", "/home/unitree/go2_voice/voice_control_board.py"])
        self.assertIn("--hands-free --volume 10", service["ExecStart"])
        self.assertIn("--mic-device plughw:CARD=Device,DEV=0", service["ExecStart"])
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(service["RestartSec"], "90")
        self.assertEqual(service["KillMode"], "mixed")
        self.assertEqual(service["TimeoutStopSec"], "20")
        self.assertTrue({"130", "143"}.issubset(service["SuccessExitStatus"].split()))
        self.assertEqual(service["StandardInput"], "null")

    def test_deployed_python_files_accept_board_python38_grammar(self):
        for filename in ("voice_control_board.py", "voice_control_board_runtime.py",
                         "install_voice_service.py"):
            with self.subTest(filename=filename):
                ast.parse((ROOT / filename).read_text(encoding="utf-8"),
                          filename=filename, feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
