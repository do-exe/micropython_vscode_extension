// Shared TypeScript types for MicroPython AI commands and MCP setup.
// Keep command/tool input shapes here so the coordinator and worker modules
// use the same contracts.

export type MicroPythonRunAndTestInput = {
  port?: string;
  localFile?: string;
  code?: string;
  projectFolder?: string;
  remoteRoot?: string;
  syncProject?: boolean;
  deleteExtraneous?: boolean;
  timeoutSeconds?: number;
};

export type MicroPythonSyncProjectInput = {
  port?: string;
  projectFolder?: string;
  remoteRoot?: string;
  deleteExtraneous?: boolean;
};

export type MicroPythonDeviceStatusInput = {
  port?: string;
};

export type MicroPythonFilesystemInput = {
  port?: string;
  operation: "list" | "read" | "write" | "mkdir" | "rename" | "delete" | "stat";
  path?: string;
  newPath?: string;
  content?: string;
  contentBase64?: string;
  recursive?: boolean;
  overwrite?: boolean;
};

export type MicroPythonSoftResetInput = {
  port?: string;
  timeoutSeconds?: number;
};

export type StringInputOptions = {
  title: string;
  prompt: string;
  placeHolder?: string;
  trim?: boolean;
  allowEmpty?: boolean;
};

export type StringInputResult = {
  value: string;
  interactive: boolean;
};

export type BackendOkResult = {
  ok?: boolean;
  error?: string;
};

export type AgentMcpLaunchConfig = {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  backendPythonPaths: string[];
};

export type CodexMcpConfigTarget = {
  path: string;
  required: boolean;
};

export type AgentMcpStatus = {
  ok: boolean;
  server: string;
  vscodeLanguageModelTools: { available: boolean; registered: boolean };
  vscodeExtensionMcpProvider: { available: boolean; registered: boolean };
  vscodeWorkspaceMcp: { configured: boolean; path: string; error?: string };
  codexGlobalMcp: { configured: boolean; paths: string[]; checkedPaths: string[]; error?: string };
  guidance: string[];
};
