import pathlib
import sys
import tempfile
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import esp_idf


class EspIdfToolchainTests(unittest.TestCase):
    def test_default_paths_point_to_local_ignored_toolchain(self) -> None:
        self.assertTrue(str(esp_idf._idf_path()).endswith("toolchain/esp-idf"))
        self.assertTrue(str(esp_idf._tools_path()).endswith("toolchain/espressif"))

    def test_status_reports_missing_local_copy_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = esp_idf.status(idf_path=str(pathlib.Path(temp_dir) / "esp-idf"), tools_path=str(pathlib.Path(temp_dir) / "espressif"))

        self.assertFalse(result["ok"])
        self.assertFalse(result["installed"])
        self.assertIn("does not exist", result["error"])


if __name__ == "__main__":
    unittest.main()
