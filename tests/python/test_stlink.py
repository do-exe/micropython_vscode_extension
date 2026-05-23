import pathlib
import sys
import tempfile
import unittest
from unittest import mock


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import stlink


class StlinkCommandTests(unittest.TestCase):
    def test_normalize_target_family(self) -> None:
        self.assertEqual(stlink.normalize_target_config("stm32f4"), "target/stm32f4x.cfg")
        self.assertEqual(stlink.normalize_target_config("target/stm32g0x.cfg"), "target/stm32g0x.cfg")
        self.assertEqual(stlink.normalize_target_config("stm32h7x.cfg"), "target/stm32h7x.cfg")

    def test_openocd_command_uses_scripts_dir_and_target(self) -> None:
        command = stlink.openocd_command(
            "stm32f4",
            ["init", "shutdown"],
            openocd_path="/tmp/runtime/linux-x64/bin/openocd",
            scripts_dir="/tmp/runtime/linux-x64/share/openocd/scripts",
        )

        self.assertEqual(command, [
            "/tmp/runtime/linux-x64/bin/openocd",
            "-s",
            "/tmp/runtime/linux-x64/share/openocd/scripts",
            "-f",
            "interface/stlink.cfg",
            "-f",
            "target/stm32f4x.cfg",
            "-c",
            "init",
            "-c",
            "shutdown",
        ])

    def test_flash_bin_adds_flash_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            firmware = pathlib.Path(temp_dir) / "firmware.bin"
            firmware.write_bytes(b"firmware")

            with mock.patch.object(stlink, "_which", return_value="/opt/runtime/bin/openocd"), \
                    mock.patch.object(stlink, "_openocd_scripts_dir", return_value="/opt/runtime/share/openocd/scripts"), \
                    mock.patch.object(stlink, "_run_process") as run_process:
                run_process.return_value = {"ok": True, "command": [], "returnCode": 0, "stdout": "", "stderr": "", "error": None}

                result = stlink.flash_firmware("stm32f4", str(firmware), address="0x08000000")

        self.assertTrue(result["ok"])
        command = run_process.call_args.args[0]
        self.assertIn(f'program "{firmware.resolve()}" 0x08000000 verify reset', command)

    def test_flash_elf_does_not_add_flash_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            firmware = pathlib.Path(temp_dir) / "firmware.elf"
            firmware.write_bytes(b"firmware")

            with mock.patch.object(stlink, "_which", return_value="/opt/runtime/bin/openocd"), \
                    mock.patch.object(stlink, "_openocd_scripts_dir", return_value="/opt/runtime/share/openocd/scripts"), \
                    mock.patch.object(stlink, "_run_process") as run_process:
                run_process.return_value = {"ok": True, "command": [], "returnCode": 0, "stdout": "", "stderr": "", "error": None}

                result = stlink.flash_firmware("stm32f4", str(firmware))

        self.assertTrue(result["ok"])
        command = run_process.call_args.args[0]
        self.assertIn(f'program "{firmware.resolve()}" verify reset', command)


if __name__ == "__main__":
    unittest.main()
