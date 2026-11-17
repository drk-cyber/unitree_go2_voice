"""Check the ASR call against the actual bundled 2.0.1 Python wrapper.

Native model execution is not possible on this Windows host.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
import zipfile
import tempfile
import os


class BoardAsrApiTest(unittest.TestCase):
    def test_tts_skips_lfs_pointer(self):
        root = Path(__file__).parent
        source = ast.parse((root / "voice_control_board_runtime.py").read_text(encoding="utf-8"))
        select = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                      and n.name == "select_tts_model")
        env = {"os": os}
        exec(compile(ast.Module(body=[select], type_ignores=[]), "board-model", "exec"), env)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "model.int8.onnx").write_bytes(b"version https://git-lfs.github.com/spec/v1\n")
            with self.assertRaises(FileNotFoundError):
                env["select_tts_model"](directory)
            (path / "model.onnx").write_bytes(b"\x08\x08model-fixture")
            self.assertEqual(env["select_tts_model"](directory), str(path / "model.onnx"))
            (path / "model.int8.onnx").write_bytes(b"\x08\x08quantized-fixture")
            self.assertEqual(env["select_tts_model"](directory), str(path / "model.int8.onnx"))

    def test_load_asr_uses_real_wrapper_factory(self):
        root = Path(__file__).parent
        wheel = next((root.parent / "board_wheels").glob("sherpa_onnx-2.0.1-*.whl"))
        with zipfile.ZipFile(wheel) as archive:
            tree = ast.parse(archive.read("sherpa_onnx/offline_recognizer.py"))
        wrapper = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                       and n.name == "OfflineRecognizer")
        factory = next(n for n in wrapper.body if isinstance(n, ast.FunctionDef)
                       and n.name == "from_sense_voice")
        wrapper.body = [factory]
        def config(**kwargs):
            return SimpleNamespace(**kwargs)
        namespace = {name: config for name in (
            "OfflineModelConfig", "OfflineSenseVoiceModelConfig",
            "FeatureExtractorConfig", "OfflineRecognizerConfig",
            "HomophoneReplacerConfig")}
        namespace["_Recognizer"] = lambda cfg: cfg
        exec(compile(ast.Module(body=[wrapper], type_ignores=[]), "wheel-wrapper", "exec"), namespace)
        api = SimpleNamespace(**namespace)
        source = ast.parse((root / "voice_control_board_runtime.py").read_text(encoding="utf-8"))
        load = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "load_asr")
        env = {"sherpa_onnx": api, "SENSE_DIR": "models/sensevoice",
               "os": SimpleNamespace(path=SimpleNamespace(
                   join=lambda *parts: "/".join(parts), exists=lambda path: True))}
        exec(compile(ast.Module(body=[load], type_ignores=[]), "board-load-asr", "exec"), env)
        env["load_asr"]()
        result = env["_recognizer"]
        self.assertEqual(result.config.model_config.sense_voice.language, "")
        self.assertTrue(result.config.model_config.sense_voice.use_itn)


if __name__ == "__main__":
    unittest.main()
