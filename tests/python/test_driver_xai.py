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

from python_service.driver_xai import DriverXaiCatalog, execute_module, prepare_bundle, validate_catalog
from python_service.driver_xai.execute import build_execute_plan
from python_service.driver_xai.mcp_tools import DriverXaiMcpTools


CATALOG_ROOT = pathlib.Path(__file__).resolve().parents[2] / "vendor" / "driver_xAI"


class DriverXaiCatalogTests(unittest.TestCase):
    def test_catalog_validates_submodule_structure(self) -> None:
        result = validate_catalog(str(CATALOG_ROOT))

        self.assertTrue(result["ok"], result)
        self.assertIn("ads1115", result["modules"])
        self.assertIn("led", result["modules"])
        self.assertIn("protocol:i2c", result["registries"])
        self.assertIn("interface:gpio", result["registries"])

    def test_catalog_search_and_info_use_module_identity_rule(self) -> None:
        catalog = DriverXaiCatalog(str(CATALOG_ROOT))

        matches = catalog.search("ads1115")
        info = catalog.info("ads1115")

        self.assertEqual(matches[0]["module_id"], "ads1115")
        self.assertEqual(info["module_id"], "ads1115")
        self.assertEqual(info["module_name"], "ads1115")

    def test_catalog_inspect_lists_drivers_and_commands(self) -> None:
        catalog = DriverXaiCatalog(str(CATALOG_ROOT))

        result = catalog.inspect("led")

        self.assertIn("micropython.py", result["drivers"])
        self.assertIn("set_color", result["commands"])
        self.assertTrue(result["read_only"])

    def test_catalog_get_driver_accepts_friendly_aliases(self) -> None:
        catalog = DriverXaiCatalog(str(CATALOG_ROOT))

        source = catalog.get("led", False, None, "driver", "driver")
        micropython_source = catalog.get("led", False, None, "micropython", "driver")

        self.assertIn("class Driver", source)
        self.assertEqual(source, micropython_source)

    def test_driver_xai_get_reports_available_sources_for_unknown_driver(self) -> None:
        result = DriverXaiMcpTools().call_tool(
            "driver_xai_get",
            {
                "moduleId": "led",
                "source": "not_a_driver",
                "fileType": "driver",
                "catalogRoot": str(CATALOG_ROOT),
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "unknown_source")
        self.assertEqual(result["requestedSource"], "not_a_driver")
        self.assertIn("micropython.py", result["availableSources"])
        self.assertEqual(result["suggestedSource"], "micropython.py")

    def test_prepare_bundle_generates_micro_python_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = pathlib.Path(temp_dir) / "driver_xai_bundle"

            result = prepare_bundle(
                ["led"],
                str(bundle_dir),
                catalog_root=str(CATALOG_ROOT),
                parameters={"led": {"channels": {"red": 12, "green": 13, "blue": 14}}},
            )

            self.assertTrue(result["ok"])
            self.assertTrue((bundle_dir / "driver_xai.py").is_file())
            self.assertTrue((bundle_dir / "modules" / "base.py").is_file())
            self.assertTrue((bundle_dir / "modules" / "led" / "drivers" / "micropython.py").is_file())
            self.assertFalse((bundle_dir / "main.py").exists())
            config = json.loads((bundle_dir / "driver_xai_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["modules"]["led"]["channels"]["red"], 12)

    def test_execute_plan_reports_missing_hardware_setup(self) -> None:
        catalog = DriverXaiCatalog(str(CATALOG_ROOT))

        result = build_execute_plan(
            catalog,
            module_id="led",
            setup={},
            command="set_color",
            command_parameters={"name": "red"},
        )

        self.assertTrue(result["problems"])
        self.assertEqual(result["problems"][0]["code"], "missing_setup_value")
        self.assertIn("hardwareFlow", result)

    def test_execute_module_syncs_bundle_and_runs_generated_main(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.sync_calls = []
                self.run_calls = []

            def sync_folder(self, **kwargs):
                self.sync_calls.append(kwargs)
                callback = kwargs.get("progress_callback")
                if callback:
                    callback("uploaded bundle")
                return {"ok": True}

            def run_file(self, **kwargs):
                self.run_calls.append(kwargs)
                output = (
                    "DRIVER_XAI_RESULT:"
                    + json.dumps({"ok": True, "result": {"red": 40, "green": 0, "blue": 0}})
                    + "\n"
                )
                return {"ok": True, "output": output, "localFile": kwargs["local_file"]}

        with tempfile.TemporaryDirectory() as temp_dir:
            result = execute_module(
                FakeSession(),
                port="/dev/ttyACM0",
                module_id="led",
                setup={
                    "channels": {"red": 44, "green": 43, "blue": 2},
                    "common_type": "common_cathode",
                },
                command="set_rgb",
                command_parameters={"red": 40, "green": 0, "blue": 0},
                catalog_root=str(CATALOG_ROOT),
                output_dir=str(pathlib.Path(temp_dir) / "bundle"),
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["driverResult"], {"red": 40, "green": 0, "blue": 0})
        self.assertIn("hardwareFlow", result)


if __name__ == "__main__":
    unittest.main()
