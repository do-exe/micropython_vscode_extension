import pathlib
import sys
import tempfile
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import arduino
from scripts import install_arduino_toolchain


class ArduinoToolchainTests(unittest.TestCase):
    def test_default_toolchain_path_points_to_local_ignored_folder(self) -> None:
        path = arduino._toolchain_root()

        self.assertTrue(str(path).endswith("toolchain/arduino"))

    def test_machine_asset_name_uses_linux_x64_archive(self) -> None:
        self.assertEqual(
            install_arduino_toolchain.machine_asset_name("1.5.0"),
            "arduino-cli_1.5.0_Linux_64bit.tar.gz",
        )

    def test_status_reports_missing_toolchain_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = arduino.status(toolchain_path=temp_dir)

        self.assertFalse(result["ok"])
        self.assertFalse(result["installed"])


if __name__ == "__main__":
    unittest.main()
