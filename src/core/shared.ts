export const SELECTED_PORT_KEY = "selectedPort";
export const SYNC_FOLDER_HISTORY_KEY = "syncFolderHistory";
export const LINKED_SYNC_FOLDER_KEY = "linkedSyncFolder";
export const POLL_INTERVAL_MS = 1000;
export const BACKEND_TIMEOUT_BUFFER_SEC = 30;
export const SESSION_RETRY_BACKOFF_MS = 3000;
export const MAX_SYNC_FOLDER_HISTORY = 2;

export type DeviceInfo = {
  port: string;
  product: string;
  description: string;
};

export type ScanResult = {
  ok: boolean;
  devices?: DeviceInfo[];
  error?: string;
};

export type SoftResetResult = {
  ok: boolean;
  promptSeen: boolean;
  rebootSeen?: boolean;
  port: string;
  output: string;
  error?: string;
};

export type RunFileResult = {
  ok: boolean;
  port: string;
  localFile: string;
  output: string;
  cancelled?: boolean;
  error?: string;
};

export type RunInteractiveFileResult = {
  ok: boolean;
  port: string;
  localFile: string;
  error?: string;
};

export type SyncFolderResult = {
  ok: boolean;
  port: string;
  localFolder: string;
  remoteFolder: string;
  filesSynced?: number;
  filesDeleted?: number;
  filesSkipped?: number;
  filesTotal?: number;
  directoriesEnsured?: number;
  bytesSynced?: number;
  error?: string;
};

export type ClearAllFilesResult = {
  ok: boolean;
  port: string;
  filesDeleted?: number;
  directoriesDeleted?: number;
  warningsReported?: number;
  bootCreated?: boolean;
  error?: string;
};

export type WorkspaceImportResult = {
  ok: boolean;
  port: string;
  localFolder: string;
  filesImported?: number;
  directoriesImported?: number;
  bytesImported?: number;
  error?: string;
};

export type WorkspaceErrorCode =
  | "ENOENT"
  | "EEXIST"
  | "ENOTDIR"
  | "EISDIR"
  | "ENOTEMPTY"
  | "ENOSPC"
  | "EPERM"
  | "EINVAL";

export type WorkspaceEntryKind = "directory" | "file";

export type WorkspaceTreeEntry = {
  path: string;
  kind: WorkspaceEntryKind;
  size?: number;
};

export type WorkspaceDirectoryEntry = {
  name: string;
  path: string;
  kind: WorkspaceEntryKind;
  size?: number;
  ctime?: number;
  mtime?: number;
};

export type WorkspaceStat = {
  path: string;
  kind: WorkspaceEntryKind;
  size: number;
  ctime?: number;
  mtime?: number;
};

export type WorkspaceTreeResult = {
  ok: boolean;
  port: string;
  entries?: WorkspaceTreeEntry[];
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceDirectoryResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  entries?: WorkspaceDirectoryEntry[];
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceStatResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  stat?: WorkspaceStat;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceFileResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  contentBase64?: string;
  size?: number;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceWriteFileResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  size?: number;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceCreateDirectoryResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceDeleteResult = {
  ok: boolean;
  port: string;
  remotePath: string;
  kind?: WorkspaceEntryKind;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type WorkspaceRenameResult = {
  ok: boolean;
  port: string;
  oldPath: string;
  newPath: string;
  code?: WorkspaceErrorCode;
  error?: string;
};

export type SyncFolderSelection = {
  localFolder: string;
  remoteFolder: string;
  deleteExtraneous: boolean;
};

export type RunCancelResult = {
  ok: boolean;
  active?: boolean;
  cancelled?: boolean;
  requestId?: string;
  error?: string;
};

export type SessionState = {
  connected: boolean;
  port?: string | null;
  error?: string;
  reason?: string;
};

export type SessionResult = SessionState & {
  ok: boolean;
};

export type TerminalWriteResult = {
  ok: boolean;
  error?: string;
};

export type BackendReadyMessage = {
  type: "ready";
};

export type BackendStreamMessage = {
  id: string;
  type: "stream";
  stream: "stdout" | "stderr";
  line: string;
};

export type BackendResultMessage = {
  id: string;
  type: "result";
  payload: unknown;
};

export type BackendTerminalOutputEventMessage = {
  type: "event";
  event: "terminal-output";
  data: string;
};

export type BackendSessionEventMessage = {
  type: "event";
  event: "session";
  payload: SessionState;
};

export type BackendMessage =
  | BackendReadyMessage
  | BackendStreamMessage
  | BackendResultMessage
  | BackendTerminalOutputEventMessage
  | BackendSessionEventMessage;

export type PendingBackendRequest<T> = {
  resolve: (payload: T) => void;
  reject: (error: Error) => void;
  onStream?: (line: string, isError: boolean) => void;
};

export type ProcessResult = {
  code: number;
  stdout: string;
  stderr: string;
};

export type PollOptions = {
  forceSessionConnect?: boolean;
  allowSessionConnect?: boolean;
  showTerminalOnConnect?: boolean;
};

export type EnsureSessionOptions = {
  force?: boolean;
  notifyOnError?: boolean;
  showTerminal?: boolean;
};
