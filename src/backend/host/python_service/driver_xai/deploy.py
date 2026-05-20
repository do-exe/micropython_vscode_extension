from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .catalog import DriverXaiError


def deploy_bundle(
    session: Any,
    *,
    port: str | None,
    bundle_dir: str,
    remote_root: str = "/",
    delete_extraneous: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    bundle_root = Path(bundle_dir).expanduser().resolve()
    if not bundle_root.is_dir():
        raise DriverXaiError(f"Driver xAI bundle directory not found: {bundle_root}")
    if not (bundle_root / "driver_xai.lock.json").is_file():
        raise DriverXaiError(f"Driver xAI bundle lock file not found: {bundle_root / 'driver_xai.lock.json'}")

    result = session.sync_folder(
        port=port,
        local_folder=str(bundle_root),
        remote_folder=remote_root,
        delete_extraneous=delete_extraneous,
        progress_callback=progress_callback,
    )
    return {
        "ok": bool(result.get("ok")),
        "port": port,
        "bundleDir": str(bundle_root),
        "remoteRoot": remote_root,
        "deleteExtraneous": delete_extraneous,
        "result": result,
    }
