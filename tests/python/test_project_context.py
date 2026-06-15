import json
import pathlib
import sys
import tempfile
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import project_context


class ProjectContextTests(unittest.TestCase):
    def test_read_can_create_default_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = project_context.read(temp_dir, create_if_missing=True, framework="arduino")
            path = pathlib.Path(temp_dir) / "project.json"

            self.assertTrue(result["ok"], result)
            self.assertTrue(result["created"])
            self.assertEqual(result["context"]["framework"], "arduino")
            self.assertTrue(path.is_file())

    def test_update_deep_merges_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_context.read(temp_dir, create_if_missing=True, framework="arduino")
            result = project_context.update(
                temp_dir,
                {
                    "board": {"fqbn": "arduino:avr:uno"},
                    "modules": [{"id": "ads1115", "address": "0x48"}],
                },
            )
            saved = json.loads((pathlib.Path(temp_dir) / "project.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(saved["board"]["fqbn"], "arduino:avr:uno")
        self.assertEqual(saved["modules"][0]["id"], "ads1115")


if __name__ == "__main__":
    unittest.main()
