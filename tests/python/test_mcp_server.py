import pathlib
import sys
import tempfile
import unittest


BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "backend"
PYTHON_SERVICE_PARENT = BACKEND_ROOT / "host"
for path in (str(PYTHON_SERVICE_PARENT), str(BACKEND_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from python_service import mcp_server


class FakeSession:
    def __init__(self, *, sync_result: dict | None = None, run_result: dict | None = None):
        self.sync_result = sync_result or {"ok": True}
        self.run_result = run_result or {"ok": True, "output": "ok"}
        self.sync_calls: list[dict] = []
        self.run_calls: list[dict] = []
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

    def test_driver_xai_tools_are_listed(self) -> None:
        session = FakeSession()
        server = self.create_server(session)

        names = {tool["name"] for tool in server._tools()}

        self.assertIn("driver_xai_search", names)
        self.assertIn("driver_xai_prepare_bundle", names)
        self.assertIn("driver_xai_deploy_bundle", names)
        self.assertIn("driver_xai_execute", names)

    def test_driver_xai_search_tool_returns_catalog_matches(self) -> None:
        session = FakeSession()
        server = self.create_server(session)

        result = server._call_tool("driver_xai_search", {"query": "ads1115"})
        payload = result["content"][0]["text"]

        self.assertFalse(result["isError"], payload)
        self.assertIn("ads1115", payload)

    def test_driver_xai_deploy_bundle_releases_serial_session(self) -> None:
        session = FakeSession()
        server = self.create_server(session)

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = pathlib.Path(temp_dir) / "bundle"
            bundle_dir.mkdir()
            (bundle_dir / "driver_xai.lock.json").write_text("{}", encoding="utf-8")

            result = server._call_tool(
                "driver_xai_deploy_bundle",
                {
                    "port": "/dev/ttyACM0",
                    "bundleDir": str(bundle_dir),
                },
            )

        self.assertFalse(result["isError"], result["content"][0]["text"])
        self.assertEqual(len(session.sync_calls), 1)
        self.assertEqual(session.close_calls, [{
            "emitEvent": False,
            "reason": mcp_server.MCP_TOOL_SESSION_RELEASE_REASON,
        }])


if __name__ == "__main__":
    unittest.main()
