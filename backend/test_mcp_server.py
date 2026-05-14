import pathlib
import sys
import tempfile
import unittest
import json


BACKEND_DIR = pathlib.Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mcp_server
from driver_xAI_adapter import DriverXAIAdapter


class FakeSession:
    def __init__(self, *, sync_result: dict | None = None, run_result: dict | None = None):
        self.sync_result = sync_result or {"ok": True}
        self.run_result = run_result or {"ok": True, "output": "ok"}
        self.sync_calls: list[dict] = []
        self.run_calls: list[dict] = []
        self.run_sources: list[str] = []
        self.close_calls: list[dict] = []

    def sync_folder(
        self,
        port: str | None,
        local_folder: str,
        remote_folder: str,
        delete_extraneous: bool = False,
        progress_callback=None,
    ) -> dict:
        self.sync_calls.append({
            "port": port,
            "localFolder": local_folder,
            "remoteFolder": remote_folder,
            "deleteExtraneous": delete_extraneous,
        })
        if progress_callback is not None:
            progress_callback("sync progress")
        return self.sync_result

    def run_file(
        self,
        port: str | None,
        local_file: str,
        timeout_seconds: float,
        stdout_line_callback=None,
        stderr_line_callback=None,
    ) -> dict:
        self.run_calls.append({
            "port": port,
            "localFile": local_file,
            "timeoutSeconds": timeout_seconds,
        })
        self.run_sources.append(pathlib.Path(local_file).read_text(encoding="utf-8"))
        if stdout_line_callback is not None:
            stdout_line_callback("stdout line")
        return self.run_result

    def close(self, emit_event: bool = True, reason: str = "closed") -> dict:
        self.close_calls.append({
            "emitEvent": emit_event,
            "reason": reason,
        })
        return {"ok": True, "connected": False, "port": None}


class McpServerPortReleaseTests(unittest.TestCase):
    def create_server(self, session: FakeSession) -> mcp_server.MicroPythonMcpServer:
        server = mcp_server.MicroPythonMcpServer()
        server._session = session
        return server

    def test_sync_project_releases_serial_session_after_success(self) -> None:
        session = FakeSession()
        server = self.create_server(session)

        with tempfile.TemporaryDirectory() as project_folder:
            result = server._tool_sync_project({
                "port": "/dev/ttyACM0",
                "projectFolder": project_folder,
            })

        self.assertTrue(result["ok"])
        self.assertTrue(result["portReleasedAfterTool"])
        self.assertEqual(len(session.sync_calls), 1)
        self.assertEqual(session.close_calls, [{
            "emitEvent": False,
            "reason": mcp_server.MCP_TOOL_SESSION_RELEASE_REASON,
        }])

    def test_run_and_test_releases_serial_session_after_success(self) -> None:
        session = FakeSession()
        server = self.create_server(session)

        result = server._tool_run_and_test({
            "port": "/dev/ttyACM0",
            "code": "print('ok')",
            "syncProject": False,
        })

        self.assertTrue(result["ok"])
        self.assertTrue(result["portReleasedAfterTool"])
        self.assertEqual(len(session.run_calls), 1)
        self.assertEqual(session.close_calls, [{
            "emitEvent": False,
            "reason": mcp_server.MCP_TOOL_SESSION_RELEASE_REASON,
        }])

    def test_run_and_test_releases_serial_session_when_sync_fails(self) -> None:
        session = FakeSession(sync_result={"ok": False, "error": "device busy"})
        server = self.create_server(session)

        with tempfile.TemporaryDirectory() as project_folder:
            result = server._tool_run_and_test({
                "port": "/dev/ttyACM0",
                "projectFolder": project_folder,
                "syncProject": True,
            })

        self.assertFalse(result["ok"])
        self.assertEqual(result["failedStep"], "syncProject")
        self.assertTrue(result["portReleasedAfterTool"])
        self.assertEqual(len(session.sync_calls), 1)
        self.assertEqual(session.run_calls, [])
        self.assertEqual(session.close_calls, [{
            "emitEvent": False,
            "reason": mcp_server.MCP_TOOL_SESSION_RELEASE_REASON,
        }])


class DriverXAIMcpTests(unittest.TestCase):
    def create_server(
        self,
        session: FakeSession,
        adapter: DriverXAIAdapter | None = None,
    ) -> mcp_server.MicroPythonMcpServer:
        server = mcp_server.MicroPythonMcpServer()
        server._session = session
        if adapter is not None:
            server._driver_xAI = adapter
        return server

    def test_module_catalog_exposes_rgb_led(self) -> None:
        server = self.create_server(FakeSession())
        result = server._tool_module_catalog({"moduleType": "rgb_led"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["modules"][0]["type"], "rgb_led")
        self.assertIn("set_color", [command["name"] for command in result["modules"][0]["commands"]])

    def test_hardware_configure_and_list_exposes_rgb_led_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DriverXAIAdapter(config_path=pathlib.Path(tmpdir) / "hardware.json")
            server = self.create_server(FakeSession(), adapter)

            result = server._tool_hardware_configure({
                "action": "add",
                "moduleId": "board_rgb",
                "moduleType": "rgb_led",
                "pins": {"red": 2, "green": 44, "blue": 43},
            })
            listed = server._tool_hardware_list({})

        self.assertTrue(result["ok"])
        self.assertEqual(result["hardware"]["id"], "board_rgb")
        self.assertEqual(result["hardware"]["pins"], {"red": 2, "green": 44, "blue": 43})
        self.assertEqual(listed["hardware"][0]["id"], "board_rgb")
        self.assertIn("set_color", [command["name"] for command in listed["hardware"][0]["commands"]])

    def test_hardware_configure_ads1115_uses_i2c_template_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DriverXAIAdapter(config_path=pathlib.Path(tmpdir) / "hardware.json")
            server = self.create_server(FakeSession(), adapter)

            result = server._tool_hardware_configure({
                "action": "add",
                "moduleId": "board_ads1115",
                "moduleType": "ads1115",
                "pins": {"sda": 16, "scl": 2},
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["hardware"]["id"], "board_ads1115")
        self.assertEqual(result["hardware"]["protocol"], "i2c")
        self.assertEqual(result["hardware"]["pins"], {"sda": 16, "scl": 2})
        self.assertEqual(result["hardware"]["options"]["address"], 0x48)
        self.assertEqual(result["hardware"]["options"]["frequency_hz"], 400000)
        self.assertEqual(result["hardware"]["options"]["gain"], 1)
        self.assertEqual(result["hardware"]["options"]["data_rate_sps"], 128)

    def test_hardware_run_uses_saved_rgb_led_instance(self) -> None:
        payload = {
            "ok": True,
            "moduleId": "board_rgb",
            "moduleType": "rgb_led",
            "command": "set_color",
            "result": {"red": 255, "green": 0, "blue": 0},
        }
        session = FakeSession(run_result={
            "ok": True,
            "output": "__MICROPYTHON_PERIPHERAL_RESULT__" + json.dumps(payload),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DriverXAIAdapter(config_path=pathlib.Path(tmpdir) / "hardware.json")
            adapter.add_hardware(
                module_id="board_rgb",
                module_type="rgb_led",
                pins={"red": 2, "green": 44, "blue": 43},
            )
            server = self.create_server(session, adapter)
            result = server._tool_hardware_run({
                "port": "/dev/ttyACM0",
                "moduleId": "board_rgb",
                "command": "set_color",
                "inputs": {"name": "red"},
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {"red": 255, "green": 0, "blue": 0})
        self.assertEqual(len(session.run_calls), 1)
        self.assertIn('\\"red\\": 2', session.run_sources[0])
        self.assertIn('\\"green\\": 44', session.run_sources[0])
        self.assertIn('\\"blue\\": 43', session.run_sources[0])
        self.assertEqual(session.close_calls, [{
            "emitEvent": False,
            "reason": mcp_server.MCP_TOOL_SESSION_RELEASE_REASON,
        }])

    def test_hardware_run_uses_saved_ads1115_i2c_instance(self) -> None:
        payload = {
            "ok": True,
            "moduleId": "board_ads1115",
            "moduleType": "ads1115",
            "command": "info",
            "result": {"address": 72, "gain": 1, "data_rate_sps": 128},
        }
        session = FakeSession(run_result={
            "ok": True,
            "output": "__MICROPYTHON_PERIPHERAL_RESULT__" + json.dumps(payload),
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = DriverXAIAdapter(config_path=pathlib.Path(tmpdir) / "hardware.json")
            adapter.add_hardware(
                module_id="board_ads1115",
                module_type="ads1115",
                pins={"sda": 16, "scl": 2},
            )
            server = self.create_server(session, adapter)
            result = server._tool_hardware_run({
                "port": "/dev/ttyACM0",
                "moduleId": "board_ads1115",
                "command": "info",
                "inputs": {},
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {"address": 72, "gain": 1, "data_rate_sps": 128})
        self.assertEqual(len(session.run_calls), 1)
        self.assertIn("from machine import Pin, I2C", session.run_sources[0])
        self.assertIn('\\"sda\\": 16', session.run_sources[0])
        self.assertIn('\\"scl\\": 2', session.run_sources[0])
        self.assertIn('\\"address\\": 72', session.run_sources[0])
        self.assertIn('\\"frequency_hz\\": 400000', session.run_sources[0])


if __name__ == "__main__":
    unittest.main()
