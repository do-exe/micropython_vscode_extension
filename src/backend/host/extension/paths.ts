import * as path from "path";

export const BACKEND_ROOT_RELATIVE_DIR = path.join("src", "backend");
export const PYTHON_SERVICE_PARENT_RELATIVE_DIR = path.join(BACKEND_ROOT_RELATIVE_DIR, "host");
export const BACKEND_SERVICE_MODULE = "python_service";
export const MCP_ADAPTER_MODULE = "python_service.mcp_server";

export function resolveExtensionBackendPath(extensionPath: string, relativePath: string): string {
  return path.join(extensionPath, relativePath);
}

export function withBackendPythonPath(env: NodeJS.ProcessEnv, backendPythonPaths: string[]): NodeJS.ProcessEnv {
  return {
    ...env,
    PYTHONPATH: [...backendPythonPaths, env.PYTHONPATH]
      .filter((value): value is string => Boolean(value))
      .join(path.delimiter),
  };
}
