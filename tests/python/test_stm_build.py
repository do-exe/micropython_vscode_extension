import pathlib
import sys
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import stm_build


class StmBuildTests(unittest.TestCase):
    def test_default_toolchain_path_points_to_local_ignored_folder(self) -> None:
        path = stm_build._toolchain_bin_dir()

        self.assertEqual(path.name, "bin")
        self.assertTrue(str(path).endswith("toolchain/arm-none-eabi/bin"))

    def test_unsupported_build_target_fails_cleanly(self) -> None:
        result = stm_build.build_firmware(".", target="stm32f4")

        self.assertFalse(result["ok"])
        self.assertIn("Unsupported STM build target", result["error"])


if __name__ == "__main__":
    unittest.main()
