import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import {
  type ClearAllFilesResult,
  LINKED_SYNC_FOLDER_KEY,
  MAX_SYNC_FOLDER_HISTORY,
  POLL_INTERVAL_MS,
  SELECTED_PORT_KEY,
  SESSION_RETRY_BACKOFF_MS,
  SYNC_FOLDER_HISTORY_KEY,
  type DeviceInfo,
  type EnsureSessionOptions,
  type PollOptions,
  type RunFileResult,
  type RunInteractiveFileResult,
  type SessionState,
  type SoftResetResult,
  type WorkspaceCreateDirectoryResult,
  type WorkspaceDeleteResult,
  type WorkspaceDirectoryEntry,
  type WorkspaceDirectoryResult,
  type WorkspaceImportResult,
  type WorkspaceRenameResult,
  type SyncFolderResult,
  type SyncFolderSelection,
  type WorkspaceStat,
  type WorkspaceStatResult,
  type WorkspaceTreeEntry,
  type WorkspaceTreeResult,
  type WorkspaceWriteFileResult,
} from "../core/shared";
import { BackendServiceClient } from "../backend/backendServiceClient";
import { MicroPythonReplPseudoterminal } from "../ui/replTerminal";
import { MicroPythonActionsViewProvider } from "../ui/actionsView";
import {
  MicroPythonWorkspaceFileSystemProvider,
  createMicroPythonWorkspaceChildUri,
  createMicroPythonWorkspaceError,
  createMicroPythonWorkspaceUri,
  getMicroPythonWorkspaceErrorCode,
  getMicroPythonWorkspaceParentUri,
  normalizeMicroPythonRemotePath,
  parseMicroPythonWorkspaceUri,
} from "../ui/workspaceFileSystemProvider";
import { type WorkspaceSelectionMode, MicroPythonWorkspaceItem, MicroPythonWorkspaceViewProvider } from "../ui/workspaceView";

const MAX_PROGRESS_MESSAGE_LENGTH = 100;
const SESSION_OPEN_WAIT_MS = 5000;
const AUTO_SAVE_SYNC_DELAY_MS = 600;
const LINKED_FOLDER_SYNC_DELAY_MS = 800;
const LINKED_FOLDER_SYNC_RETRY_DELAY_MS = 2000;

type DocumentSyncState = "pending" | "syncing" | "synced" | "error";

type WorkspaceCommandTarget = {
  remotePath?: string;
  port?: string;
  kind?: "file" | "folder" | "placeholder";
};

export class MicroPythonExtensionController implements vscode.Disposable {
  private readonly backend: BackendServiceClient;
  private readonly statusItem: vscode.StatusBarItem;
  private readonly runItem: vscode.StatusBarItem;
  private readonly runInteractiveItem: vscode.StatusBarItem;
  private readonly workspaceSyncItem: vscode.StatusBarItem;
  private readonly runOutput: vscode.OutputChannel;
  private readonly syncOutput: vscode.OutputChannel;
  private readonly cleanupOutput: vscode.OutputChannel;
  private readonly workspaceOutput: vscode.OutputChannel;
  private readonly workspaceFetchOutput: vscode.OutputChannel;
  private readonly workspaceTreeView: vscode.TreeView<MicroPythonWorkspaceItem>;
  private readonly workspaceViewProvider: MicroPythonWorkspaceViewProvider;
  private readonly workspaceFileSystemProvider: MicroPythonWorkspaceFileSystemProvider;

  private pollTimer: NodeJS.Timeout | undefined;
  private pollInFlight = false;
  private operationInFlight = 0;
  private runInFlight = false;
  private sessionOpenInFlight = false;
  private backendReady = false;

  private devices: DeviceInfo[] = [];
  private selectedPort: string | undefined;
  private sessionState: SessionState = { connected: false };

  private replTerminal: vscode.Terminal | undefined;
  private replPty: MicroPythonReplPseudoterminal | undefined;
  private disposing = false;

  private lastSessionAttemptAt = 0;
  private lastSessionAttemptPort: string | undefined;
  private lastSessionError: string | undefined;
  private recentTerminalOutput = "";
  private terminalInteractionInFlight = false;
  private terminalInputQueue: Promise<void> = Promise.resolve();
  private disconnectHandlingInFlight = false;
  private activeWorkspaceTarget: WorkspaceCommandTarget | undefined;
  private workspaceClipboardSource: vscode.Uri | undefined;
  private lastWorkspaceDestinationFolder: string | undefined;
  private linkedSyncSelection: SyncFolderSelection | undefined;
  private linkedFolderWatcher: vscode.Disposable | undefined;
  private linkedFolderSyncTimer: NodeJS.Timeout | undefined;
  private linkedFolderSyncQueued = false;
  private linkedFolderSyncInFlight = false;
  private linkedFolderSyncState: DocumentSyncState | undefined;
  private linkedFolderSyncError: string | undefined;
  private readonly autoSaveTimers = new Map<string, NodeJS.Timeout>();
  private readonly workspaceSyncState = new Map<string, DocumentSyncState>();

  constructor(private readonly context: vscode.ExtensionContext) {
    this.backend = new BackendServiceClient(context);

    this.statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.statusItem.command = "micropython.selectDevice";
    this.statusItem.show();

    this.runItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    this.runItem.command = "micropython.runCurrentFile";
    this.runItem.text = "$(play) Run Non-Interactive";
    this.runItem.tooltip = "Run active Python file on MicroPython through raw REPL";

    this.runInteractiveItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 98);
    this.runInteractiveItem.command = "micropython.runInteractiveFile";
    this.runInteractiveItem.text = "$(terminal) Run Interactive";
    this.runInteractiveItem.tooltip = "Run active Python file on MicroPython through the normal REPL";

    this.workspaceSyncItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 97);
    this.workspaceSyncItem.hide();

    this.runOutput = vscode.window.createOutputChannel("Run Non-Interactive File");
    this.syncOutput = vscode.window.createOutputChannel("MicroPython Folder Sync");
    this.cleanupOutput = vscode.window.createOutputChannel("MicroPython Clear All Files");
    this.workspaceOutput = vscode.window.createOutputChannel("MicroPython Workspace");
    this.workspaceFetchOutput = vscode.window.createOutputChannel("MicroPython Workspace Fetch");
    this.workspaceFileSystemProvider = new MicroPythonWorkspaceFileSystemProvider({
      stat: async (uri: vscode.Uri) => this.statWorkspaceUri(uri),
      readDirectory: async (uri: vscode.Uri) => this.readWorkspaceDirectoryUri(uri),
      readFile: async (uri: vscode.Uri) => this.readWorkspaceFileUri(uri),
      writeFile: async (uri: vscode.Uri, content: Uint8Array, options) => this.writeWorkspaceFileUri(uri, content, options),
      createDirectory: async (uri: vscode.Uri) => this.createWorkspaceDirectoryUri(uri),
      delete: async (uri: vscode.Uri, options) => this.deleteWorkspaceEntryUri(uri, options.recursive),
      rename: async (oldUri: vscode.Uri, newUri: vscode.Uri, options) => this.renameWorkspaceEntryUri(oldUri, newUri, options.overwrite),
    });
    this.workspaceViewProvider = new MicroPythonWorkspaceViewProvider({
      scanTree: async () => this.scanWorkspaceTree(),
      shouldAutoLoad: () => this.shouldAutoScanWorkspace(),
    });
    this.workspaceTreeView = vscode.window.createTreeView("micropython.workspaceView", {
      treeDataProvider: this.workspaceViewProvider,
      manageCheckboxStateManually: true,
    });
    this.selectedPort = this.context.globalState.get<string>(SELECTED_PORT_KEY);
    this.setRunVisible(false);
    void this.setWorkspaceClipboard(undefined);
    void this.updateWorkspaceSelectionState();

    this.context.subscriptions.push(
      this.backend.onTerminalOutput((data: string) => {
        this.handleTerminalOutput(data);
      }),
      this.backend.onSessionState((state: SessionState) => {
        this.handleSessionStateChange(state);
      }),
      vscode.workspace.registerFileSystemProvider("micropython", this.workspaceFileSystemProvider, {
        isCaseSensitive: true,
      }),
      vscode.window.registerTreeDataProvider("micropython.actionsView", new MicroPythonActionsViewProvider()),
      this.workspaceTreeView,
      this.workspaceViewProvider.onDidChangeSelectionState(() => {
        void this.updateWorkspaceSelectionState();
      }),
      this.workspaceTreeView.onDidChangeCheckboxState((event: vscode.TreeCheckboxChangeEvent<MicroPythonWorkspaceItem>) => {
        this.workspaceViewProvider.handleCheckboxStateChange(event.items);
      }),
      this.workspaceTreeView.onDidChangeSelection((event: vscode.TreeViewSelectionChangeEvent<MicroPythonWorkspaceItem>) => {
        this.activeWorkspaceTarget = this.toWorkspaceCommandTarget(event.selection[0]);
      }),
      this.workspaceFileSystemProvider.onDidChangeFile((events: readonly vscode.FileChangeEvent[]) => {
        const hasStructuralChange = events.some((event) => event.type !== vscode.FileChangeType.Changed);
        if (!hasStructuralChange) {
          return;
        }
        this.workspaceViewProvider.invalidate(true);
      }),
      vscode.workspace.onDidChangeTextDocument((event: vscode.TextDocumentChangeEvent) => {
        this.handleWorkspaceTextChanged(event);
      }),
      vscode.workspace.onDidSaveTextDocument((document: vscode.TextDocument) => {
        this.handleTextDocumentSaved(document);
      }),
      vscode.workspace.onDidCloseTextDocument((document: vscode.TextDocument) => {
        this.clearAutoSaveTimer(document.uri);
        this.workspaceSyncState.delete(document.uri.toString());
        this.updateWorkspaceSyncStatus();
      }),
      vscode.window.onDidChangeActiveTextEditor(() => {
        this.updateWorkspaceSyncStatus();
      }),
      vscode.window.onDidCloseTerminal((terminal: vscode.Terminal) => {
        if (terminal !== this.replTerminal) {
          return;
        }
        this.replTerminal = undefined;
        if (this.replPty) {
          this.replPty.dispose();
          this.replPty = undefined;
        }
        if (!this.disposing) {
          void this.handleReplTerminalClosed();
        }
      }),
    );
  }

  public async start(): Promise<void> {
    this.context.subscriptions.push(
      this.statusItem,
      this.runItem,
      this.runInteractiveItem,
      this.workspaceSyncItem,
      this.runOutput,
      this.syncOutput,
      this.cleanupOutput,
      this.workspaceOutput,
      this.workspaceFetchOutput,
    );
    this.registerCommands();
    this.setInitializingStatus();

    try {
      await this.backend.ensureReady();
      this.backendReady = true;
      this.sessionState = await this.backend.getSessionState();
    } catch (error) {
      this.setNoDeviceStatus("MicroPython backend setup failed");
      void vscode.window.showErrorMessage(this.errorMessage(error, "MicroPython backend setup failed."));
      return;
    }

    await this.restoreLinkedFolderSelection();

    this.refreshStatus();
    this.workspaceViewProvider.invalidate();
    const autoConnect = this.shouldAutoConnectOnDetect();
    await this.pollDevices({
      forceSessionConnect: autoConnect,
      allowSessionConnect: autoConnect,
      showTerminalOnConnect: autoConnect,
    });
    this.pollTimer = setInterval(() => {
      void this.pollDevices({ allowSessionConnect: this.shouldAutoConnectOnDetect() });
    }, POLL_INTERVAL_MS);
  }

  public dispose(): void {
    this.disposing = true;
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = undefined;
    }
    if (this.linkedFolderSyncTimer) {
      clearTimeout(this.linkedFolderSyncTimer);
      this.linkedFolderSyncTimer = undefined;
    }
    this.disposeLinkedFolderWatcher();
    for (const timer of this.autoSaveTimers.values()) {
      clearTimeout(timer);
    }
    this.autoSaveTimers.clear();

    if (this.replTerminal) {
      this.replTerminal.dispose();
      this.replTerminal = undefined;
    }
    if (this.replPty) {
      this.replPty.dispose();
      this.replPty = undefined;
    }

    this.backend.dispose();
    this.statusItem.dispose();
    this.runItem.dispose();
    this.runInteractiveItem.dispose();
    this.workspaceSyncItem.dispose();
    this.runOutput.dispose();
    this.syncOutput.dispose();
    this.cleanupOutput.dispose();
    this.workspaceOutput.dispose();
    this.workspaceFetchOutput.dispose();
  }

  private registerCommands(): void {
    this.context.subscriptions.push(
      vscode.commands.registerCommand("micropython.selectDevice", async () => {
        await this.selectDevice();
      }),
      vscode.commands.registerCommand("micropython.openTerminal", async () => {
        await this.openTerminal();
      }),
      vscode.commands.registerCommand("micropython.softResetDevice", async () => {
        await this.softResetDevice();
      }),
      vscode.commands.registerCommand("micropython.runCurrentFile", async () => {
        await this.runCurrentFile();
      }),
      vscode.commands.registerCommand("micropython.runInteractiveFile", async () => {
        await this.runInteractiveFile();
      }),
      vscode.commands.registerCommand("micropython.linkFolder", async () => {
        await this.linkFolderCommand();
      }),
      vscode.commands.registerCommand("micropython.syncFolder", async (uri?: vscode.Uri) => {
        await this.syncFolderCommand(uri);
      }),
      vscode.commands.registerCommand("micropython.fetchWorkspace", async () => {
        await this.fetchWorkspaceCommand();
      }),
      vscode.commands.registerCommand("micropython.fetchWorkspacePartial", async () => {
        await this.fetchWorkspacePartialCommand();
      }),
      vscode.commands.registerCommand("micropython.fetchWorkspacePartialConfirm", async () => {
        await this.confirmWorkspacePartialFetchCommand();
      }),
      vscode.commands.registerCommand("micropython.fetchWorkspacePartialClear", async () => {
        await this.clearWorkspacePartialFetchSelectionCommand();
      }),
      vscode.commands.registerCommand("micropython.fetchWorkspacePartialCancel", async () => {
        await this.cancelWorkspacePartialFetchCommand();
      }),
      vscode.commands.registerCommand("micropython.deleteWorkspaceSelection", async () => {
        await this.deleteWorkspaceSelectionCommand();
      }),
      vscode.commands.registerCommand("micropython.deleteWorkspaceSelectionConfirm", async () => {
        await this.confirmWorkspaceDeleteSelectionCommand();
      }),
      vscode.commands.registerCommand("micropython.deleteWorkspaceSelectionClear", async () => {
        await this.clearWorkspaceDeleteSelectionCommand();
      }),
      vscode.commands.registerCommand("micropython.deleteWorkspaceSelectionCancel", async () => {
        await this.cancelWorkspaceDeleteSelectionCommand();
      }),
      vscode.commands.registerCommand("micropython.clearAllFiles", async () => {
        await this.clearAllFilesCommand();
      }),
      vscode.commands.registerCommand("micropython.refreshWorkspace", async () => {
        await this.refreshWorkspaceCommand();
      }),
      vscode.commands.registerCommand("micropython.newWorkspaceFile", async (target?: WorkspaceCommandTarget) => {
        await this.createWorkspaceFileCommand(target);
      }),
      vscode.commands.registerCommand("micropython.newWorkspaceFolder", async (target?: WorkspaceCommandTarget) => {
        await this.createWorkspaceFolderCommand(target);
      }),
      vscode.commands.registerCommand("micropython.copyWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.copyWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.pasteWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.pasteWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.renameWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.renameWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.deleteWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.deleteWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.showWorkspaceEntryProperties", async (target?: WorkspaceCommandTarget) => {
        await this.showWorkspaceEntryPropertiesCommand(target);
      }),
      vscode.commands.registerCommand("micropython.uploadWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.uploadWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.downloadWorkspaceEntry", async (target?: WorkspaceCommandTarget) => {
        await this.downloadWorkspaceEntryCommand(target);
      }),
      vscode.commands.registerCommand("micropython.mountWorkspace", async () => {
        await this.mountWorkspaceCommand();
      }),
      vscode.commands.registerCommand("micropython.openWorkspaceFile", async (remotePath: string, port?: string) => {
        await this.openWorkspaceFileCommand(remotePath, port);
      }),
    );
  }

  private async selectDevice(): Promise<void> {
    if (!this.backendReady) {
      return;
    }

    await this.pollDevices();
    if (this.devices.length === 0) {
      void vscode.window.showWarningMessage("No MicroPython device detected. Connect the device, then run Select Device again.");
      return;
    }

    if (this.devices.length === 1) {
      await this.persistSelectedPort(this.devices[0].port);
      this.showReplTerminal(false);
      await this.pollDevices();
      return;
    }

    const picks = this.devices.map((device) => ({
      label: device.port,
      detail: this.formatDevicePickerDetail(device),
      port: device.port,
    }));

    const choice = await vscode.window.showQuickPick(picks, {
      title: "Select Device",
      placeHolder: "Choose a detected device port",
      ignoreFocusOut: true,
    });

    if (!choice) {
      return;
    }

    await this.persistSelectedPort(choice.port);
    this.showReplTerminal(false);
    await this.pollDevices();
    void vscode.window.showInformationMessage(`Device selected: ${choice.port}`);
  }

  private formatDevicePickerDetail(device: DeviceInfo): string {
    const parts = [device.product, device.description].map((s) => s?.trim()).filter(Boolean);
    return parts.join(" — ") || device.port;
  }

  private async softResetDevice(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.operationInFlight > 0) {
      const settled = await this.waitForOperationToSettle(2000);
      if (!settled) {
        void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
        return;
      }
    }

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
    if (!connected) {
      return;
    }

    const timeout = vscode.workspace.getConfiguration("micropython").get<number>("resetTimeoutSeconds", 5);

    let result: SoftResetResult;
    try {
      this.operationInFlight += 1;
      this.refreshStatus();
      result = await this.backend.softReset(port, timeout);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Soft reset failed."));
      return;
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }

    if (!result.ok) {
      const recovered = await this.restartBackendAndReconnect(port);
      if (!recovered) {
        const detail = result.error ? ` ${result.error}` : "";
        void vscode.window.showErrorMessage(`Soft reset failed on ${port}.${detail}`);
        return;
      }

      try {
        this.operationInFlight += 1;
        this.refreshStatus();
        result = await this.backend.softReset(port, timeout);
      } catch (error) {
        void vscode.window.showErrorMessage(this.errorMessage(error, "Soft reset recovery retry failed."));
        return;
      } finally {
        this.operationInFlight = Math.max(0, this.operationInFlight - 1);
        this.refreshStatus();
        await this.pollDevices();
      }
    }

    if (result.ok) {
      this.workspaceViewProvider.invalidate(true);
      if (this.shouldAutoScanWorkspace()) {
        try {
          await this.refreshWorkspaceCommand();
        } catch {
          // Ignore refresh failures; soft reset itself already completed.
        }
      }

      if (result.promptSeen) {
        void vscode.window.showInformationMessage(`Soft reset complete on ${port}. MicroPython prompt verified.`);
      } else if (result.rebootSeen) {
        void vscode.window.showInformationMessage(`Soft reset complete on ${port}. Device reboot detected.`);
      } else {
        void vscode.window.showInformationMessage(`Soft reset complete on ${port}.`);
      }
      return;
    }

    const detail = result.error ? ` ${result.error}` : "";
    void vscode.window.showErrorMessage(`Soft reset failed on ${port}.${detail}`);
  }

  private async runCurrentFile(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("A MicroPython non-interactive run is already in progress.");
      return;
    }
    if (this.operationInFlight > 0) {
      const settled = await this.waitForOperationToSettle(2000);
      if (!settled) {
        void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
        return;
      }
    }

    this.runInFlight = true;
    this.setRunButtonBusy(true);

    try {
      let localFile: string | undefined;
      try {
        localFile = await this.resolveLocalFileForRun();
      } catch (error) {
        void vscode.window.showErrorMessage(this.errorMessage(error, "Run aborted."));
        return;
      }
      if (!localFile) {
        return;
      }

      let port: string;
      try {
        port = await this.resolvePortForOperation();
      } catch (error) {
        void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
        return;
      }

      const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
      if (!connected) {
        return;
      }

      const timeout = vscode.workspace.getConfiguration("micropython").get<number>("runTimeoutSeconds", 0);
      let result: RunFileResult;

      try {
        this.operationInFlight += 1;
        this.refreshStatus();
        result = await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `MicroPython: Running ${path.basename(localFile)} non-interactively on ${port}`,
            cancellable: true,
          },
          async (_progress, cancelToken) => {
            this.runOutput.clear();
            this.runOutput.appendLine(`MicroPython non-interactive run on ${port}`);
            this.runOutput.appendLine(`File: ${localFile}`);
            this.runOutput.appendLine("");
            this.runOutput.show(false);

            const pendingLines: string[] = [];
            let flushTimer: NodeJS.Timeout | undefined;

            const flush = (): void => {
              if (pendingLines.length === 0) {
                return;
              }
              const batch = pendingLines.splice(0);
              for (const line of batch) {
                this.runOutput.appendLine(line);
              }
            };

            flushTimer = setInterval(flush, 16);

            try {
              return await this.backend.runFileStreaming(
                port,
                localFile,
                timeout,
                (line: string, isError: boolean) => {
                  pendingLines.push(isError ? `[ERROR] ${line}` : line);
                },
                cancelToken,
              );
            } finally {
              if (flushTimer) {
                clearInterval(flushTimer);
              }
              flush();
            }
          },
        );
      } catch (error) {
        void vscode.window.showErrorMessage(this.errorMessage(error, "Run failed."));
        return;
      } finally {
        this.operationInFlight = Math.max(0, this.operationInFlight - 1);
        this.refreshStatus();
      }

      await this.pollDevices();
      this.runOutput.show(false);

      if (result.cancelled) {
        void vscode.window.showInformationMessage(`Non-interactive run cancelled on ${port}: ${path.basename(localFile)}`);
        return;
      }

      if (!result.ok) {
        const detail = result.error ? ` ${result.error}` : "";
        void vscode.window.showErrorMessage(`Non-interactive run failed on ${port}.${detail}`);
        return;
      }

      void vscode.window.showInformationMessage(`Non-interactive run complete on ${port}: ${path.basename(localFile)}`);
    } finally {
      this.runInFlight = false;
      this.setRunButtonBusy(false);
    }
  }

  private async runInteractiveFile(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("A MicroPython non-interactive run is already in progress.");
      return;
    }
    if (this.operationInFlight > 0) {
      void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
      return;
    }

    let localFile: string | undefined;
    try {
      localFile = await this.resolveLocalFileForRun();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Interactive run aborted."));
      return;
    }
    if (!localFile) {
      return;
    }

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
    if (!connected) {
      return;
    }

    let result: RunInteractiveFileResult;
    try {
      this.operationInFlight += 1;
      this.refreshStatus();
      this.showReplTerminal(false);
      result = await this.backend.runFileInteractive(port, localFile);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Interactive run failed."));
      return;
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
    }

    await this.pollDevices();

    if (!result.ok) {
      const detail = result.error ? ` ${result.error}` : "";
      void vscode.window.showErrorMessage(`Interactive run failed on ${port}.${detail}`);
      return;
    }

    this.showReplTerminal(false);
    void vscode.window.showInformationMessage(
      `Interactive run started on ${port}: ${path.basename(localFile)}.`,
    );
  }

  private async syncFolderCommand(folderUri?: vscode.Uri): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("MicroPython is busy with a run operation.");
      return;
    }
    if (this.operationInFlight > 0) {
      void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
      return;
    }

    let selection: SyncFolderSelection | undefined;
    try {
      selection = await this.resolveFolderForSync(folderUri);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Folder sync aborted."));
      return;
    }
    if (!selection) {
      return;
    }

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
    if (!connected) {
      return;
    }

    let result: SyncFolderResult;
    try {
      result = await this.executeFolderSyncWithSelection(port, selection, {
        title: `MicroPython: Syncing ${path.basename(selection.localFolder)} to ${port}`,
        revealOutput: true,
        showProgress: true,
      });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Folder sync failed."));
      return;
    }

    this.syncOutput.show(false);

    if (!result.ok) {
      const detail = result.error ? ` ${result.error}` : "";
      void vscode.window.showErrorMessage(`Folder sync failed on ${port}.${detail}`);
      return;
    }

    await this.afterFolderSyncSuccess(selection, true);
    const fileCount = result.filesSynced ?? 0;
    const deletedCount = result.filesDeleted ?? 0;
    const skippedCount = result.filesSkipped ?? 0;
    const totalBytes = result.bytesSynced ?? 0;
    void vscode.window.showInformationMessage(
      `MicroPython sync complete: ${fileCount} uploaded, ${deletedCount} deleted, ${skippedCount} skipped to ${result.remoteFolder} (${this.formatByteCount(totalBytes)} sent).`,
    );
  }

  private async linkFolderCommand(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("MicroPython is busy with a run operation.");
      return;
    }
    if (this.operationInFlight > 0) {
      void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
      return;
    }

    const localFolder = await this.pickFolderFromDialog();
    if (!localFolder) {
      return;
    }

    const selection = await this.buildSyncFolderSelection(localFolder);

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
    if (!connected) {
      return;
    }

    let result: SyncFolderResult;
    try {
      result = await this.executeFolderSyncWithSelection(port, selection, {
        title: `MicroPython: Linking ${path.basename(selection.localFolder)} to ${port}`,
        revealOutput: true,
        showProgress: true,
      });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Linked folder sync failed."));
      return;
    }

    this.syncOutput.show(false);

    if (!result.ok) {
      const detail = result.error ? ` ${result.error}` : "";
      void vscode.window.showErrorMessage(`Linked folder sync failed on ${port}.${detail}`);
      return;
    }

    await this.afterFolderSyncSuccess(selection, true);
    await this.setLinkedFolderSelection(selection);
    void vscode.window.showInformationMessage(
      `Linked folder active: ${selection.localFolder}. New or saved files in this folder will sync automatically to ${selection.remoteFolder}.`,
    );
  }

  private async executeFolderSyncWithSelection(
    port: string,
    selection: SyncFolderSelection,
    options: {
      title: string;
      revealOutput: boolean;
      showProgress: boolean;
    },
  ): Promise<SyncFolderResult> {
    const runSync = async (progress?: vscode.Progress<{ message?: string }>): Promise<SyncFolderResult> => {
      this.syncOutput.clear();
      this.syncOutput.appendLine(`MicroPython folder sync on ${port}`);
      this.syncOutput.appendLine(`Local:  ${selection.localFolder}`);
      this.syncOutput.appendLine(`Remote: ${selection.remoteFolder}`);
      this.syncOutput.appendLine(
        `Mode:   ${selection.deleteExtraneous ? "mirror sync (delete stale remote files)" : "upload only"}`,
      );
      this.syncOutput.appendLine("");
      if (options.revealOutput) {
        this.syncOutput.show(false);
      }

      return this.backend.syncFolder(
        port,
        selection.localFolder,
        selection.remoteFolder,
        selection.deleteExtraneous,
        (line: string, isError: boolean) => {
          const formatted = isError ? `[ERROR] ${line}` : line;
          this.syncOutput.appendLine(formatted);
          progress?.report({ message: formatted.slice(0, MAX_PROGRESS_MESSAGE_LENGTH) });
        },
      );
    };

    this.operationInFlight += 1;
    this.refreshStatus();
    try {
      if (options.showProgress) {
        return await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: options.title,
            cancellable: false,
          },
          async (progress) => runSync(progress),
        );
      }
      return await runSync();
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }
  }

  private async afterFolderSyncSuccess(selection: SyncFolderSelection, rememberHistory: boolean): Promise<void> {
    if (rememberHistory) {
      await this.rememberSyncFolder(selection.localFolder);
    }
    this.workspaceViewProvider.invalidate(true);
    if (this.shouldAutoScanWorkspace()) {
      try {
        await this.refreshWorkspaceCommand();
      } catch {
        // Ignore refresh failures; sync itself already completed.
      }
    }
  }

  private async clearAllFilesCommand(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("MicroPython is busy with a run operation.");
      return;
    }
    if (this.operationInFlight > 0) {
      void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
      return;
    }

    const confirmation = await vscode.window.showWarningMessage(
      "Delete all files from the selected MicroPython device and recreate an empty boot.py?",
      {
        modal: true,
        detail: "This mirrors the desktop app workflow and removes the current device workspace.",
      },
      "Delete All Files",
    );
    if (confirmation !== "Delete All Files") {
      return;
    }

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    const connected = await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: true });
    if (!connected) {
      return;
    }

    let result: ClearAllFilesResult;
    try {
      this.operationInFlight += 1;
      this.refreshStatus();
      result = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `MicroPython: Clearing all files on ${port}`,
          cancellable: false,
        },
        async (progress) => {
          this.cleanupOutput.clear();
          this.cleanupOutput.appendLine(`MicroPython clear-all on ${port}`);
          this.cleanupOutput.appendLine("Workflow: desktop-style recursive cleanup + empty boot.py restore");
          this.cleanupOutput.appendLine("");
          this.cleanupOutput.show(false);

          return this.backend.clearAllFiles(port, (line: string, isError: boolean) => {
            const formatted = isError ? `[ERROR] ${line}` : line;
            this.cleanupOutput.appendLine(formatted);
            progress.report({ message: formatted.slice(0, MAX_PROGRESS_MESSAGE_LENGTH) });
          });
        },
      );
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Clear all files failed."));
      return;
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }

    this.cleanupOutput.show(false);

    if (!result.ok) {
      const detail = result.error ? ` ${result.error}` : "";
      void vscode.window.showErrorMessage(`Clear all files failed on ${port}.${detail}`);
      return;
    }

    this.workspaceViewProvider.invalidate();
    if (this.shouldAutoScanWorkspace()) {
      try {
        await this.refreshWorkspaceCommand();
      } catch {
        // Ignore post-clean refresh failures; the clear operation already succeeded.
      }
    }

    const filesDeleted = result.filesDeleted ?? 0;
    const directoriesDeleted = result.directoriesDeleted ?? 0;
    const warningsReported = result.warningsReported ?? 0;
    if (warningsReported > 0) {
      void vscode.window.showWarningMessage(
        `MicroPython clear complete: ${filesDeleted} files deleted, ${directoriesDeleted} folders deleted, ${warningsReported} warning(s). Empty boot.py restored.`,
      );
      return;
    }

    void vscode.window.showInformationMessage(
      `MicroPython clear complete: ${filesDeleted} files deleted, ${directoriesDeleted} folders deleted. Empty boot.py restored.`,
    );
  }

  private async refreshWorkspaceCommand(): Promise<void> {
    await this.workspaceViewProvider.reload();
  }

  private async createWorkspaceFileCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const effectiveTarget = this.getWorkspaceCommandTarget(target);
    const port = await this.prepareWorkspaceCommandPort(effectiveTarget?.port, false);
    if (!port) {
      return;
    }

    const parentUri = this.resolveWorkspaceDirectoryUri(effectiveTarget, port);
    const fileName = await this.promptWorkspaceName(
      "MicroPython: New File",
      "Enter the new file name",
      "main.py",
    );
    if (!fileName) {
      return;
    }

    const fileUri = createMicroPythonWorkspaceChildUri(parentUri, fileName);
    try {
      if (await this.workspaceUriExists(fileUri)) {
        void vscode.window.showWarningMessage(`A MicroPython file named ${fileName} already exists.`);
        return;
      }
      await vscode.workspace.fs.writeFile(fileUri, new Uint8Array());
      const document = await vscode.workspace.openTextDocument(fileUri);
      await vscode.window.showTextDocument(document, { preview: false });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, `Failed to create ${fileName}.`));
    }
  }

  private async createWorkspaceFolderCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const effectiveTarget = this.getWorkspaceCommandTarget(target);
    const port = await this.prepareWorkspaceCommandPort(effectiveTarget?.port, false);
    if (!port) {
      return;
    }

    const parentUri = this.resolveWorkspaceDirectoryUri(effectiveTarget, port);
    const folderName = await this.promptWorkspaceName(
      "MicroPython: New Folder",
      "Enter the new folder name",
      "folder",
    );
    if (!folderName) {
      return;
    }

    const folderUri = createMicroPythonWorkspaceChildUri(parentUri, folderName);
    try {
      if (await this.workspaceUriExists(folderUri)) {
        void vscode.window.showWarningMessage(`A MicroPython folder named ${folderName} already exists.`);
        return;
      }
      await vscode.workspace.fs.createDirectory(folderUri);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, `Failed to create ${folderName}.`));
    }
  }

  private async copyWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const targetUri = await this.resolveWorkspaceEntryUri(target);
    if (!targetUri) {
      return;
    }

    const { remotePath } = parseMicroPythonWorkspaceUri(targetUri);
    if (remotePath === "/") {
      void vscode.window.showWarningMessage("The MicroPython device root cannot be copied.");
      return;
    }

    await this.setWorkspaceClipboard(targetUri);
    vscode.window.setStatusBarMessage(`Copied ${path.posix.basename(remotePath)}.`, 2000);
  }

  private async pasteWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const sourceUri = this.workspaceClipboardSource;
    if (!sourceUri) {
      return;
    }

    let sourceStat: vscode.FileStat;
    try {
      sourceStat = await vscode.workspace.fs.stat(sourceUri);
    } catch (error) {
      await this.setWorkspaceClipboard(undefined);
      void vscode.window.showWarningMessage(this.errorMessage(error, "The copied MicroPython item no longer exists."));
      return;
    }

    const sourceTarget = parseMicroPythonWorkspaceUri(sourceUri);
    const effectiveTarget = this.getWorkspaceCommandTarget(target);
    const port = await this.prepareWorkspaceCommandPort(effectiveTarget?.port ?? sourceTarget.port, false);
    if (!port) {
      return;
    }
    if (port !== sourceTarget.port) {
      void vscode.window.showWarningMessage("Paste currently works only on the same MicroPython device.");
      return;
    }

    const destinationDirectoryUri = this.resolveWorkspaceDirectoryUri(effectiveTarget, port);
    const sourceName = path.posix.basename(sourceTarget.remotePath);
    if (!sourceName) {
      void vscode.window.showWarningMessage("The copied MicroPython entry is not valid.");
      return;
    }

    const destinationUri = await this.createPasteDestinationUri(destinationDirectoryUri, sourceName, sourceStat);
    const destinationTarget = parseMicroPythonWorkspaceUri(destinationUri);
    if (destinationTarget.remotePath.startsWith(`${sourceTarget.remotePath}/`)) {
      void vscode.window.showWarningMessage("You cannot paste a folder into itself.");
      return;
    }

    try {
      await this.copyWorkspaceUri(sourceUri, destinationUri);
      await this.setWorkspaceClipboard(undefined);
      void vscode.window.showInformationMessage(`Pasted ${path.posix.basename(destinationTarget.remotePath)}.`);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, `Failed to paste ${sourceName}.`));
    }
  }

  private async renameWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const targetUri = await this.resolveWorkspaceEntryUri(target);
    if (!targetUri) {
      return;
    }

    const { remotePath } = parseMicroPythonWorkspaceUri(targetUri);
    if (remotePath === "/") {
      void vscode.window.showWarningMessage("The MicroPython device root cannot be renamed.");
      return;
    }

    const currentName = path.posix.basename(remotePath);
    const nextName = await this.promptWorkspaceName(
      "MicroPython: Rename",
      `Enter the new name for ${currentName}`,
      currentName,
    );
    if (!nextName || nextName === currentName) {
      return;
    }

    const renamedUri = createMicroPythonWorkspaceChildUri(getMicroPythonWorkspaceParentUri(targetUri), nextName);
    try {
      await vscode.workspace.fs.rename(targetUri, renamedUri, { overwrite: false });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, `Failed to rename ${currentName}.`));
    }
  }

  private async showWorkspaceEntryPropertiesCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const targetUri = await this.resolveWorkspaceEntryUri(target);
    if (!targetUri) {
      return;
    }

    try {
      const entryStat = await vscode.workspace.fs.stat(targetUri);
      const { port, remotePath } = parseMicroPythonWorkspaceUri(targetUri);
      const name = remotePath === "/" ? "MicroPython" : path.posix.basename(remotePath);
      const parentPath = remotePath === "/" ? "/" : path.posix.dirname(remotePath);
      const isDirectory = (entryStat.type & vscode.FileType.Directory) !== 0;
      const typeLabel = isDirectory ? "Folder" : "File";
      let summaryLine = `Size: ${this.formatByteCount(entryStat.size)}`;

      if (isDirectory) {
        const childEntries = await vscode.workspace.fs.readDirectory(targetUri);
        const folderCount = childEntries.filter(([, type]) => (type & vscode.FileType.Directory) !== 0).length;
        const fileCount = childEntries.filter(([, type]) => (type & vscode.FileType.File) !== 0).length;
        summaryLine = `Contents: ${folderCount} folder(s), ${fileCount} file(s)`;
      }

      const detailLines = [
        `Name: ${name}`,
        `Type: ${typeLabel}`,
        summaryLine,
        `Modified: ${this.formatWorkspaceTimestamp(entryStat.mtime)}`,
        `Created: ${this.formatWorkspaceTimestamp(entryStat.ctime)}`,
        `Path: ${remotePath}`,
        `Folder: ${parentPath}`,
        `Device: ${port}`,
      ];

      await vscode.window.showInformationMessage(name, {
        modal: true,
        detail: detailLines.join("\n"),
      });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Failed to load workspace properties."));
    }
  }

  private async deleteWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const targetUri = await this.resolveWorkspaceEntryUri(target);
    if (!targetUri) {
      return;
    }

    const { remotePath } = parseMicroPythonWorkspaceUri(targetUri);
    if (remotePath === "/") {
      void vscode.window.showWarningMessage("The MicroPython device root cannot be deleted.");
      return;
    }

    const label = path.posix.basename(remotePath);
    const isDirectory = target?.kind === "folder";
    const confirmation = await vscode.window.showWarningMessage(
      `Delete ${isDirectory ? "folder" : "file"} ${label} from MicroPython?`,
      {
        modal: true,
        detail: isDirectory
          ? "This removes the folder and all of its contents from the device."
          : "This removes the file from the device.",
      },
      "Delete",
    );
    if (confirmation !== "Delete") {
      return;
    }

    try {
      await vscode.workspace.fs.delete(targetUri, { recursive: isDirectory, useTrash: false });
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, `Failed to delete ${label}.`));
    }
  }

  private async uploadWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const port = await this.prepareWorkspaceCommandPort(target?.port, false);
    if (!port) {
      return;
    }

    const destinationUri = this.resolveWorkspaceDirectoryUri(target, port);
    const destinationPath = parseMicroPythonWorkspaceUri(destinationUri).remotePath;
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: true,
      canSelectMany: true,
      openLabel: "Upload to MicroPython",
      title: "MicroPython: Select Files or Folders to Upload",
      defaultUri: vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file")?.uri,
    });
    if (!picked || picked.length === 0) {
      return;
    }

    try {
      const counts = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `MicroPython: Uploading to ${destinationPath}`,
          cancellable: false,
        },
        async (progress) => {
          let files = 0;
          let directories = 0;
          for (const sourceUri of picked) {
            const copied = await this.copyLocalUriToWorkspace(sourceUri, destinationUri, progress);
            files += copied.files;
            directories += copied.directories;
          }
          return { files, directories };
        },
      );

      void vscode.window.showInformationMessage(
        `MicroPython upload complete: ${counts.files} file(s) and ${counts.directories} folder(s) copied to ${destinationPath}.`,
      );
      this.workspaceViewProvider.invalidate(true);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "MicroPython upload failed."));
    }
  }

  private async downloadWorkspaceEntryCommand(target?: WorkspaceCommandTarget): Promise<void> {
    const effectiveTarget = this.getWorkspaceCommandTarget(target);
    const remotePath = normalizeMicroPythonRemotePath(effectiveTarget?.remotePath ?? "/");
    if (remotePath === "/") {
      await this.fetchWorkspaceCommand();
      return;
    }

    const port = await this.prepareWorkspaceCommandPort(effectiveTarget?.port, true);
    if (!port) {
      return;
    }

    const localFolder = await this.pickExistingWorkspaceDestinationFolder("Save MicroPython Files", "MicroPython: Select Local Folder");
    if (!localFolder) {
      return;
    }

    await this.importWorkspaceSelection(port, localFolder, [remotePath]);
  }

  private async mountWorkspaceCommand(): Promise<void> {
    const port = await this.prepareWorkspaceCommandPort(undefined, false);
    if (!port) {
      return;
    }

    const rootUri = createMicroPythonWorkspaceUri("/", port);
    const existing = vscode.workspace.workspaceFolders?.find((folder) => folder.uri.toString() === rootUri.toString());
    if (existing) {
      void vscode.window.showInformationMessage(`MicroPython workspace already mounted as ${existing.name}.`);
      return;
    }

    const index = vscode.workspace.workspaceFolders?.length ?? 0;
    const added = vscode.workspace.updateWorkspaceFolders(index, 0, {
      uri: rootUri,
      name: `MicroPython (${path.basename(port) || port})`,
    });
    if (!added) {
      void vscode.window.showErrorMessage("Failed to mount the MicroPython workspace in Explorer.");
      return;
    }

    void vscode.window.showInformationMessage(`MicroPython workspace mounted for ${port}.`);
  }

  private async fetchWorkspaceCommand(): Promise<void> {
    const port = await this.prepareWorkspaceFetchPort();
    if (!port) {
      return;
    }

    const localFolder = await this.pickExistingWorkspaceDestinationFolder("Save MicroPython Files", "MicroPython: Select Local Folder");
    if (!localFolder) {
      return;
    }

    await this.importWorkspaceSelection(port, localFolder);
  }

  private async fetchWorkspacePartialCommand(): Promise<void> {
    if (this.workspaceViewProvider.isFetchSelectionActive) {
      if (this.workspaceViewProvider.getSelectedFetchPaths().length === 0) {
        await this.clearWorkspaceFetchSession();
        void vscode.window.showInformationMessage("Download selection closed.");
        return;
      }

      await this.confirmWorkspacePartialFetchCommand();
      return;
    }

    const port = await this.prepareWorkspaceFetchPort();
    if (!port) {
      return;
    }

    if (this.workspaceViewProvider.activateFetchSelection(port)) {
      await this.updateWorkspaceSelectionState();
      await vscode.commands.executeCommand("workbench.view.extension.micropythonSidebar");
      void vscode.window.showInformationMessage(
        "Download selection is active in MicroPython Workspace. Check files or folders, then press Download again.",
      );
      return;
    }

    let entries: WorkspaceTreeEntry[];
    try {
      entries = await this.loadWorkspaceEntriesForSelection(port);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Unable to load MicroPython workspace."));
      return;
    }

    if (entries.length === 0) {
      await this.clearWorkspaceFetchSession();
      void vscode.window.showInformationMessage("MicroPython workspace is empty.");
      return;
    }

    this.workspaceViewProvider.setFetchSelectionSnapshot(port, entries);
    await this.updateWorkspaceSelectionState();

    await vscode.commands.executeCommand("workbench.view.extension.micropythonSidebar");
    void vscode.window.showInformationMessage(
      "Download selection is active in MicroPython Workspace. Check files or folders, then press Download again.",
    );
  }

  private async deleteWorkspaceSelectionCommand(): Promise<void> {
    if (this.workspaceViewProvider.isDeleteSelectionActive) {
      if (this.workspaceViewProvider.getSelectedDeletePaths().length === 0) {
        await this.clearWorkspaceDeleteSession();
        void vscode.window.showInformationMessage("Delete selection closed.");
        return;
      }

      await this.confirmWorkspaceDeleteSelectionCommand();
      return;
    }

    const port = await this.prepareWorkspaceCommandPort(undefined, false);
    if (!port) {
      return;
    }

    if (this.workspaceViewProvider.activateDeleteSelection(port)) {
      await this.updateWorkspaceSelectionState();
      await vscode.commands.executeCommand("workbench.view.extension.micropythonSidebar");
      void vscode.window.showInformationMessage(
        "Delete selection is active in MicroPython Workspace. Check files or folders, then press Delete again.",
      );
      return;
    }

    let entries: WorkspaceTreeEntry[];
    try {
      entries = await this.loadWorkspaceEntriesForSelection(port);
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Unable to load MicroPython workspace."));
      return;
    }

    if (entries.length === 0) {
      await this.clearWorkspaceDeleteSession();
      void vscode.window.showInformationMessage("MicroPython workspace is empty.");
      return;
    }

    this.workspaceViewProvider.setDeleteSelectionSnapshot(port, entries);
    await this.updateWorkspaceSelectionState();

    await vscode.commands.executeCommand("workbench.view.extension.micropythonSidebar");
    void vscode.window.showInformationMessage(
      "Delete selection is active in MicroPython Workspace. Check files or folders, then press Delete again.",
    );
  }

  private async updateWorkspaceSelectionState(): Promise<void> {
    await vscode.commands.executeCommand("setContext", "micropython.workspaceFetchSelectionActive", this.workspaceViewProvider.isFetchSelectionActive);
    await vscode.commands.executeCommand("setContext", "micropython.workspaceDeleteSelectionActive", this.workspaceViewProvider.isDeleteSelectionActive);
  }

  private async clearWorkspaceFetchSession(): Promise<void> {
    this.workspaceViewProvider.resetFetchSelection();
    await this.updateWorkspaceSelectionState();
  }

  private async clearWorkspaceDeleteSession(): Promise<void> {
    this.workspaceViewProvider.resetDeleteSelection();
    await this.updateWorkspaceSelectionState();
  }

  private async confirmWorkspacePartialFetchCommand(): Promise<void> {
    if (!this.workspaceViewProvider.isFetchSelectionActive) {
      return;
    }

    const port = this.workspaceViewProvider.fetchSelectionPort;
    if (!port) {
      await this.clearWorkspaceFetchSession();
      return;
    }

    const selectedPaths = this.workspaceViewProvider.getSelectedFetchPaths();
    if (selectedPaths.length === 0) {
      void vscode.window.showWarningMessage("Select at least one file or folder to download.");
      return;
    }

    const localFolder = await this.pickExistingWorkspaceDestinationFolder("Save MicroPython Files", "MicroPython: Select Local Folder");
    if (!localFolder) {
      return;
    }

    const success = await this.importWorkspaceSelection(port, localFolder, selectedPaths);
    if (success) {
      await this.clearWorkspaceFetchSession();
    }
  }

  private async clearWorkspacePartialFetchSelectionCommand(): Promise<void> {
    this.workspaceViewProvider.clearFetchSelection();
    await this.updateWorkspaceSelectionState();
  }

  private async cancelWorkspacePartialFetchCommand(): Promise<void> {
    await this.clearWorkspaceFetchSession();
  }

  private async confirmWorkspaceDeleteSelectionCommand(): Promise<void> {
    if (!this.workspaceViewProvider.isDeleteSelectionActive) {
      return;
    }

    const port = this.workspaceViewProvider.deleteSelectionPort;
    if (!port) {
      await this.clearWorkspaceDeleteSession();
      return;
    }

    const selectedPaths = this.workspaceViewProvider.getSelectedDeletePaths();
    if (selectedPaths.length === 0) {
      void vscode.window.showWarningMessage("Select at least one file or folder to delete.");
      return;
    }

    const confirm = await vscode.window.showWarningMessage(
      `Delete ${selectedPaths.length} selected MicroPython path(s)?`,
      { modal: true, detail: "This action cannot be undone." },
      "Delete",
    );
    if (confirm !== "Delete") {
      return;
    }

    const sortedPaths = [...selectedPaths].sort((a, b) => b.length - a.length);
    for (const remotePath of sortedPaths) {
      const result = await this.withWorkspaceBackendOperation(port, () => this.backend.deleteWorkspaceEntry(port, remotePath, true));
      if (!result.ok) {
        this.throwWorkspaceResultError(result, `Failed to delete ${remotePath}.`);
      }
    }

    await this.clearWorkspaceDeleteSession();
    this.workspaceViewProvider.invalidate(true);
    void vscode.window.showInformationMessage(`Deleted ${selectedPaths.length} selected path(s).`);
  }

  private async clearWorkspaceDeleteSelectionCommand(): Promise<void> {
    this.workspaceViewProvider.clearDeleteSelection();
    await this.updateWorkspaceSelectionState();
  }

  private async cancelWorkspaceDeleteSelectionCommand(): Promise<void> {
    await this.clearWorkspaceDeleteSession();
  }

  private async openWorkspaceFileCommand(remotePath: string, port?: string): Promise<void> {
    const resolvedPort = await this.prepareWorkspaceCommandPort(port, false);
    if (!resolvedPort) {
      return;
    }

    const fileUri = createMicroPythonWorkspaceUri(remotePath, resolvedPort);
    const document = await vscode.workspace.openTextDocument(fileUri);
    await vscode.window.showTextDocument(document, { preview: false });
  }

  private async prepareWorkspaceCommandPort(preferredPort?: string, showTerminal = false): Promise<string | undefined> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return undefined;
    }
    if (this.runInFlight) {
      void vscode.window.showWarningMessage("MicroPython is busy with a run operation.");
      return undefined;
    }
    if (this.operationInFlight > 0) {
      const settled = await this.waitForOperationToSettle(2000);
      if (!settled) {
        void vscode.window.showWarningMessage("MicroPython is busy with another operation.");
        return undefined;
      }
    }

    let port = preferredPort?.trim();
    if (!port) {
      try {
        port = await this.resolvePortForOperation();
      } catch (error) {
        void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
        return undefined;
      }
    }

    const connected = await this.ensureSessionForPort(port, {
      force: true,
      notifyOnError: true,
      showTerminal,
    });
    if (!connected) {
      return undefined;
    }
    return port;
  }

  private async prepareWorkspaceFetchPort(): Promise<string | undefined> {
    return this.prepareWorkspaceCommandPort(undefined, true);
  }

  private async loadWorkspaceEntriesForSelection(port: string): Promise<WorkspaceTreeEntry[]> {
    let result: WorkspaceTreeResult;
    try {
      this.operationInFlight += 1;
      this.refreshStatus();
      result = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: `MicroPython: Loading workspace from ${port}`,
          cancellable: false,
        },
        async () => {
          this.workspaceOutput.clear();
          this.workspaceOutput.appendLine(`MicroPython workspace scan on ${port}`);
          this.workspaceOutput.appendLine("Remote root: /");
          this.workspaceOutput.appendLine("");
          this.workspaceOutput.show(false);
          return this.backend.scanWorkspaceTree(port);
        },
      );
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }

    this.workspaceOutput.show(false);
    if (!result.ok) {
      throw new Error(result.error ?? "Failed to scan MicroPython workspace.");
    }
    return result.entries ?? [];
  }

  private async pickWorkspaceDestinationFolder(): Promise<string | undefined> {
    type DestinationChoice = vscode.QuickPickItem & {
      action: "existing" | "new";
    };

    const choice = await vscode.window.showQuickPick<DestinationChoice>([
      {
        label: "$(folder-opened) Existing Folder",
        detail: "Save the fetched MicroPython files into an existing local folder.",
        action: "existing",
      },
      {
        label: "$(new-folder) New Folder",
        detail: "Choose a parent folder, then create a new folder for the fetched MicroPython files.",
        action: "new",
      },
    ], {
      title: "MicroPython: Choose Save Location",
      placeHolder: "Select where the fetched MicroPython files should be saved",
      ignoreFocusOut: true,
    });

    if (!choice) {
      return undefined;
    }

    if (choice.action === "existing") {
      return this.pickExistingWorkspaceDestinationFolder("Save MicroPython Files", "MicroPython: Select Folder to Save Files");
    }

    const parentFolder = await this.pickExistingWorkspaceDestinationFolder("Select Parent Folder", "MicroPython: Select Parent Folder");
    if (!parentFolder) {
      return undefined;
    }

    const folderName = await vscode.window.showInputBox({
      title: "MicroPython: New Folder Name",
      prompt: "Enter the name of the new folder for fetched MicroPython files",
      ignoreFocusOut: true,
      validateInput: (value) => {
        const trimmed = value.trim();
        if (!trimmed) {
          return "Folder name is required.";
        }
        if (trimmed.includes("/") || trimmed.includes("\\")) {
          return "Enter a folder name only, not a path.";
        }
        return undefined;
      },
    });
    if (!folderName) {
      return undefined;
    }

    const destination = path.resolve(parentFolder, folderName.trim());
    try {
      await fs.promises.mkdir(destination, { recursive: false });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
        throw error;
      }
      throw new Error(`Folder already exists: ${destination}`);
    }

    this.lastWorkspaceDestinationFolder = destination;
    return destination;
  }

  private async pickExistingWorkspaceDestinationFolder(openLabel: string, title: string): Promise<string | undefined> {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel,
      title,
      defaultUri: this.getDefaultLocalFolderUri(),
    });
    if (!picked || picked.length === 0) {
      return undefined;
    }

    const localFolder = picked[0].fsPath;
    this.lastWorkspaceDestinationFolder = localFolder;
    return localFolder;
  }

  private async importWorkspaceSelection(port: string, localFolder: string, remotePaths?: string[]): Promise<boolean> {
    let result: WorkspaceImportResult;
    const fetchTitle = remotePaths && remotePaths.length > 0
      ? `MicroPython: Downloading selection from ${port}`
      : `MicroPython: Fetching workspace from ${port}`;

    try {
      this.operationInFlight += 1;
      this.refreshStatus();
      result = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: fetchTitle,
          cancellable: false,
        },
        async (progress) => {
          this.workspaceFetchOutput.clear();
          this.workspaceFetchOutput.appendLine(`MicroPython workspace fetch on ${port}`);
          this.workspaceFetchOutput.appendLine(`Local: ${localFolder}`);
          this.workspaceFetchOutput.appendLine(
            remotePaths && remotePaths.length > 0
              ? `Selection: ${remotePaths.join(", ")}`
              : "Selection: /",
          );
          this.workspaceFetchOutput.appendLine("");
          this.workspaceFetchOutput.show(false);

          return this.backend.importWorkspace(
            port,
            localFolder,
            (line: string, isError: boolean) => {
              const formatted = isError ? `[ERROR] ${line}` : line;
              this.workspaceFetchOutput.appendLine(formatted);
              progress.report({ message: formatted.slice(0, MAX_PROGRESS_MESSAGE_LENGTH) });
            },
            remotePaths,
          );
        },
      );
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "Workspace fetch failed."));
      return false;
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }

    this.workspaceFetchOutput.show(false);
    if (!result.ok) {
      const detail = result.error ? ` ${result.error}` : "";
      void vscode.window.showErrorMessage(`Workspace fetch failed on ${port}.${detail}`);
      return false;
    }

    const filesImported = result.filesImported ?? 0;
    const directoriesImported = result.directoriesImported ?? 0;
    const bytesImported = result.bytesImported ?? 0;
    void vscode.window.showInformationMessage(
      `MicroPython fetch complete: ${filesImported} files and ${directoriesImported} folders saved to ${localFolder} (${this.formatByteCount(bytesImported)}).`,
    );
    return true;
  }

  private async withWorkspaceBackendOperation<T>(port: string, operation: () => Promise<T>): Promise<T> {
    await this.ensureSessionForPort(port, { force: true, notifyOnError: true, showTerminal: false });
    this.operationInFlight += 1;
    this.refreshStatus();
    try {
      return await operation();
    } finally {
      this.operationInFlight = Math.max(0, this.operationInFlight - 1);
      this.refreshStatus();
      await this.pollDevices();
    }
  }

  private async scanWorkspaceTree(): Promise<{ port: string; entries: WorkspaceTreeEntry[] }> {
    const port = await this.resolvePortForOperation();
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.scanWorkspaceTree(port));
    if (!result.ok) {
      throw new Error(result.error ?? "Failed to load MicroPython workspace.");
    }
    return {
      port,
      entries: result.entries ?? [],
    };
  }

  private async statWorkspaceUri(uri: vscode.Uri): Promise<WorkspaceStat> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.statWorkspaceEntry(port, remotePath));
    if (!result.ok || !result.stat) {
      this.throwWorkspaceResultError(result, `Failed to stat ${remotePath}.`);
    }
    return result.stat;
  }

  private async readWorkspaceDirectoryUri(uri: vscode.Uri): Promise<WorkspaceDirectoryEntry[]> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.listWorkspaceDirectory(port, remotePath));
    if (!result.ok) {
      this.throwWorkspaceResultError(result, `Failed to list ${remotePath}.`);
    }
    return result.entries ?? [];
  }

  private async readWorkspaceFileUri(uri: vscode.Uri): Promise<Uint8Array> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.readWorkspaceFile(port, remotePath));
    if (!result.ok || typeof result.contentBase64 !== "string") {
      this.throwWorkspaceResultError(result, `Failed to read ${remotePath}.`);
    }
    return Uint8Array.from(Buffer.from(result.contentBase64, "base64"));
  }

  private async writeWorkspaceFileUri(
    uri: vscode.Uri,
    content: Uint8Array,
    options: { create: boolean; overwrite: boolean },
  ): Promise<void> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.writeWorkspaceFile(
      port,
      remotePath,
      Buffer.from(content).toString("base64"),
      options,
    ));
    if (!result.ok) {
      this.throwWorkspaceResultError(result, `Failed to write ${remotePath}.`);
    }
    this.workspaceSyncState.set(uri.toString(), "synced");
    this.updateWorkspaceSyncStatus();
  }

  private async createWorkspaceDirectoryUri(uri: vscode.Uri): Promise<void> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.createWorkspaceDirectory(port, remotePath));
    if (!result.ok) {
      this.throwWorkspaceResultError(result, `Failed to create ${remotePath}.`);
    }
  }

  private async deleteWorkspaceEntryUri(uri: vscode.Uri, recursive: boolean): Promise<void> {
    const { port, remotePath } = parseMicroPythonWorkspaceUri(uri);
    const result = await this.withWorkspaceBackendOperation(port, () => this.backend.deleteWorkspaceEntry(port, remotePath, recursive));
    if (!result.ok) {
      this.throwWorkspaceResultError(result, `Failed to delete ${remotePath}.`);
    }
  }

  private async renameWorkspaceEntryUri(oldUri: vscode.Uri, newUri: vscode.Uri, overwrite: boolean): Promise<void> {
    const oldTarget = parseMicroPythonWorkspaceUri(oldUri);
    const newTarget = parseMicroPythonWorkspaceUri(newUri);
    if (oldTarget.port !== newTarget.port) {
      throw createMicroPythonWorkspaceError("EINVAL", "MicroPython workspace rename must stay on the same device.");
    }

    const result = await this.withWorkspaceBackendOperation(oldTarget.port, () => this.backend.renameWorkspaceEntry(
      oldTarget.port,
      oldTarget.remotePath,
      newTarget.remotePath,
      overwrite,
    ));
    if (!result.ok) {
      this.throwWorkspaceResultError(result, `Failed to rename ${oldTarget.remotePath}.`);
    }
  }

  private resolveWorkspaceDirectoryUri(target: WorkspaceCommandTarget | undefined, port: string): vscode.Uri {
    const remotePath = normalizeMicroPythonRemotePath(target?.remotePath ?? "/");
    if (target?.kind === "file") {
      return createMicroPythonWorkspaceUri(path.posix.dirname(remotePath), port);
    }
    return createMicroPythonWorkspaceUri(remotePath, port);
  }

  private async resolveWorkspaceEntryUri(target?: WorkspaceCommandTarget): Promise<vscode.Uri | undefined> {
    const effectiveTarget = this.getWorkspaceCommandTarget(target);
    const remotePath = effectiveTarget?.remotePath?.trim();
    if (!remotePath) {
      void vscode.window.showWarningMessage("Select a MicroPython workspace file or folder first.");
      return undefined;
    }

    const port = await this.prepareWorkspaceCommandPort(effectiveTarget?.port, false);
    if (!port) {
      return undefined;
    }

    return createMicroPythonWorkspaceUri(remotePath, port);
  }

  private async promptWorkspaceName(title: string, prompt: string, initialValue: string): Promise<string | undefined> {
    const value = await vscode.window.showInputBox({
      title,
      prompt,
      value: initialValue,
      ignoreFocusOut: true,
      validateInput: (input) => {
        const trimmed = input.trim();
        if (!trimmed) {
          return "Name is required.";
        }
        if (trimmed === "." || trimmed === "..") {
          return "Choose a normal file or folder name.";
        }
        if (trimmed.includes("/") || trimmed.includes("\\")) {
          return "Enter a name only, not a path.";
        }
        return undefined;
      },
    });
    return value?.trim() || undefined;
  }

  private async copyLocalUriToWorkspace(
    sourceUri: vscode.Uri,
    destinationDirectoryUri: vscode.Uri,
    progress: vscode.Progress<{ message?: string }>,
  ): Promise<{ files: number; directories: number }> {
    const sourceStat = await vscode.workspace.fs.stat(sourceUri);
    const sourceName = path.posix.basename(sourceUri.path);
    if (!sourceName) {
      throw new Error(`Cannot derive a destination name for ${sourceUri.path}.`);
    }

    const targetUri = createMicroPythonWorkspaceChildUri(destinationDirectoryUri, sourceName);
    if ((sourceStat.type & vscode.FileType.Directory) !== 0) {
      progress.report({ message: `Folder: ${sourceName}` });
      await vscode.workspace.fs.createDirectory(targetUri);
      let files = 0;
      let directories = 1;
      const children = await vscode.workspace.fs.readDirectory(sourceUri);
      for (const [childName] of children) {
        const childUri = vscode.Uri.joinPath(sourceUri, childName);
        const childCounts = await this.copyLocalUriToWorkspace(childUri, targetUri, progress);
        files += childCounts.files;
        directories += childCounts.directories;
      }
      return { files, directories };
    }

    if ((sourceStat.type & vscode.FileType.File) !== 0) {
      progress.report({ message: `File: ${sourceName}` });
      const content = await vscode.workspace.fs.readFile(sourceUri);
      await vscode.workspace.fs.writeFile(targetUri, content);
      return { files: 1, directories: 0 };
    }

    throw new Error(`Unsupported local entry type: ${sourceUri.fsPath || sourceUri.path}`);
  }

  private async copyWorkspaceUri(sourceUri: vscode.Uri, destinationUri: vscode.Uri): Promise<void> {
    const sourceStat = await vscode.workspace.fs.stat(sourceUri);
    if ((sourceStat.type & vscode.FileType.Directory) !== 0) {
      await vscode.workspace.fs.createDirectory(destinationUri);
      const children = await vscode.workspace.fs.readDirectory(sourceUri);
      for (const [childName] of children) {
        await this.copyWorkspaceUri(
          vscode.Uri.joinPath(sourceUri, childName),
          vscode.Uri.joinPath(destinationUri, childName),
        );
      }
      return;
    }

    const content = await vscode.workspace.fs.readFile(sourceUri);
    await vscode.workspace.fs.writeFile(destinationUri, content);
  }

  private async createPasteDestinationUri(
    destinationDirectoryUri: vscode.Uri,
    sourceName: string,
    sourceStat: vscode.FileStat,
  ): Promise<vscode.Uri> {
    const directUri = createMicroPythonWorkspaceChildUri(destinationDirectoryUri, sourceName);
    if (!(await this.workspaceUriExists(directUri))) {
      return directUri;
    }

    const isDirectory = (sourceStat.type & vscode.FileType.Directory) !== 0;
    const parsedName = path.posix.parse(sourceName);
    const baseName = isDirectory ? sourceName : parsedName.name;
    const extension = isDirectory ? "" : parsedName.ext;

    for (let copyIndex = 1; copyIndex <= 999; copyIndex += 1) {
      const suffix = copyIndex === 1 ? " copy" : ` copy ${copyIndex}`;
      const candidateName = `${baseName}${suffix}${extension}`;
      const candidateUri = createMicroPythonWorkspaceChildUri(destinationDirectoryUri, candidateName);
      if (!(await this.workspaceUriExists(candidateUri))) {
        return candidateUri;
      }
    }

    throw new Error(`Unable to find a free paste name for ${sourceName}.`);
  }

  private async workspaceUriExists(uri: vscode.Uri): Promise<boolean> {
    try {
      await vscode.workspace.fs.stat(uri);
      return true;
    } catch (error) {
      if (getMicroPythonWorkspaceErrorCode(error) === "ENOENT") {
        return false;
      }
      if (error instanceof vscode.FileSystemError && /file not found/i.test(error.message)) {
        return false;
      }
      throw error;
    }
  }

  private getWorkspaceCommandTarget(target?: WorkspaceCommandTarget): WorkspaceCommandTarget | undefined {
    if (target?.remotePath || target?.port) {
      return target;
    }
    return this.activeWorkspaceTarget;
  }

  private toWorkspaceCommandTarget(item?: MicroPythonWorkspaceItem): WorkspaceCommandTarget | undefined {
    if (!item || item.kind === "placeholder") {
      return undefined;
    }
    return {
      remotePath: item.remotePath,
      port: item.port,
      kind: item.kind,
    };
  }

  private async setWorkspaceClipboard(source: vscode.Uri | undefined): Promise<void> {
    this.workspaceClipboardSource = source;
    await vscode.commands.executeCommand("setContext", "micropython.workspaceHasClipboard", Boolean(source));
  }

  private getDefaultLocalFolderUri(): vscode.Uri {
    if (this.lastWorkspaceDestinationFolder) {
      return vscode.Uri.file(this.lastWorkspaceDestinationFolder);
    }

    const workspaceFolder = vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file");
    if (workspaceFolder) {
      return workspaceFolder.uri;
    }

    return vscode.Uri.file(os.homedir());
  }

  private formatWorkspaceTimestamp(value: number): string {
    if (!value) {
      return "Unknown";
    }

    const milliseconds = value < 10_000_000_000 ? value * 1000 : value;
    const date = new Date(milliseconds);
    if (Number.isNaN(date.valueOf())) {
      return String(value);
    }
    return date.toLocaleString();
  }

  private throwWorkspaceResultError(
    result: {
      code?: string;
      error?: string;
    },
    fallback: string,
  ): never {
    const message = result.error?.trim() || fallback;
    if (typeof result.code === "string" && result.code.trim().length > 0) {
      throw createMicroPythonWorkspaceError(
        result.code as Parameters<typeof createMicroPythonWorkspaceError>[0],
        message,
      );
    }
    throw new Error(message);
  }

  private async openTerminal(): Promise<void> {
    if (!this.backendReady) {
      void vscode.window.showErrorMessage("MicroPython backend is still initializing.");
      return;
    }

    let port: string;
    try {
      port = await this.resolvePortForOperation();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "No device selected."));
      return;
    }

    this.showReplTerminal(false);
  }

  private queueTerminalInput(data: string): Promise<void> {
    const pending = this.terminalInputQueue.then(() => this.sendTerminalInputToDevice(data));
    this.terminalInputQueue = pending.then(
      () => undefined,
      () => undefined,
    );
    return pending;
  }

  private async sendTerminalInputToDevice(data: string): Promise<void> {
    if (!data) {
      return;
    }
    if (!this.backendReady) {
      throw new Error("MicroPython backend is still initializing.");
    }
    if (this.operationInFlight > 0 || this.sessionOpenInFlight) {
      throw new Error("MicroPython is busy with another operation.");
    }

    let port = this.selectedPort;
    const selectedSessionOpen = Boolean(port && this.sessionState.connected && this.sessionState.port === port);
    if (!selectedSessionOpen) {
      port = await this.resolvePortForOperation();
      const connected = await this.ensureSessionForPort(port, {
        force: false,
        notifyOnError: false,
        showTerminal: false,
      });
      if (!connected) {
        throw new Error(`Failed to open MicroPython session on ${port}.`);
      }
    }

    this.terminalInteractionInFlight = true;
    this.refreshStatus();

    try {
      await this.backend.sendTerminalInput(data);
      this.lastSessionError = undefined;
    } catch (error) {
      this.terminalInteractionInFlight = false;
      this.refreshStatus();
      throw error;
    }
  }

  private async resolveFolderForSync(folderUri?: vscode.Uri): Promise<SyncFolderSelection | undefined> {
    if (folderUri?.scheme === "file" && await this.isDirectoryPath(folderUri.fsPath)) {
      return this.buildSyncFolderSelection(folderUri.fsPath);
    }

    const history = await this.loadSyncFolderHistory();
    if (history.length === 0) {
      const localFolder = await this.pickFolderFromDialog();
      if (!localFolder) {
        return undefined;
      }
      return this.buildSyncFolderSelection(localFolder);
    }

    type FolderChoice = vscode.QuickPickItem & {
      folderPath?: string;
      browse?: boolean;
    };

    const picks: FolderChoice[] = [
      {
        label: "$(folder-opened) Select Folder...",
        detail: "Browse for a local folder to sync to MicroPython.",
        browse: true,
      },
    ];
    for (const folderPath of history) {
      const selection = await this.buildSyncFolderSelection(folderPath);
      picks.push({
        label: `$(history) ${path.basename(folderPath)}`,
        description: folderPath,
        detail: `${selection.deleteExtraneous ? "Mirror sync" : "Upload only"} -> ${selection.remoteFolder}`,
        folderPath,
      });
    }

    const choice = await vscode.window.showQuickPick(picks, {
      title: "MicroPython: Select Folder",
      placeHolder: "Choose a remembered folder or browse for another folder",
      ignoreFocusOut: true,
    });
    if (!choice) {
      return undefined;
    }

    const localFolder = choice.browse ? await this.pickFolderFromDialog() : choice.folderPath;
    if (!localFolder) {
      return undefined;
    }
    return this.buildSyncFolderSelection(localFolder);
  }

  private async pickFolderFromDialog(): Promise<string | undefined> {
    const workspaceFolder = vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file");
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      defaultUri: workspaceFolder?.uri,
      openLabel: "Sync to MicroPython",
      title: "MicroPython: Select Local Folder",
    });
    if (!picked || picked.length === 0) {
      return undefined;
    }
    return path.resolve(picked[0].fsPath);
  }

  private handleWorkspaceTextChanged(event: vscode.TextDocumentChangeEvent): void {
    if (event.contentChanges.length === 0) {
      return;
    }

    const document = event.document;
    if (document.uri.scheme === "micropython") {
      this.workspaceSyncState.set(document.uri.toString(), "pending");
      this.updateWorkspaceSyncStatus();
      this.scheduleDocumentAutoSave(document);
      return;
    }

    if (!this.isLinkedFolderFileUri(document.uri)) {
      return;
    }

    this.linkedFolderSyncState = "pending";
    this.linkedFolderSyncError = undefined;
    this.updateWorkspaceSyncStatus();
    this.scheduleDocumentAutoSave(document);
  }

  private handleTextDocumentSaved(document: vscode.TextDocument): void {
    this.clearAutoSaveTimer(document.uri);
    if (!this.isLinkedFolderFileUri(document.uri)) {
      return;
    }

    this.linkedFolderSyncState = "syncing";
    this.linkedFolderSyncError = undefined;
    this.updateWorkspaceSyncStatus();
  }

  private scheduleDocumentAutoSave(document: vscode.TextDocument): void {
    if (document.isUntitled || document.isClosed) {
      return;
    }

    this.clearAutoSaveTimer(document.uri);
    const timer = setTimeout(() => {
      this.autoSaveTimers.delete(document.uri.toString());
      void this.autoSaveDocument(document);
    }, AUTO_SAVE_SYNC_DELAY_MS);
    this.autoSaveTimers.set(document.uri.toString(), timer);
  }

  private clearAutoSaveTimer(uri: vscode.Uri): void {
    const key = uri.toString();
    const existing = this.autoSaveTimers.get(key);
    if (!existing) {
      return;
    }

    clearTimeout(existing);
    this.autoSaveTimers.delete(key);
  }

  private async autoSaveDocument(document: vscode.TextDocument): Promise<void> {
    if (document.isClosed || !document.isDirty) {
      return;
    }

    try {
      await document.save();
    } catch {
      // Leave the pending state visible; the next edit or manual save can retry.
    }
  }

  private updateWorkspaceSyncStatus(): void {
    const activeDocument = vscode.window.activeTextEditor?.document;
    if (!activeDocument) {
      this.workspaceSyncItem.hide();
      return;
    }

    if (activeDocument.uri.scheme === "micropython") {
      this.renderSyncStatusItem(this.workspaceSyncState.get(activeDocument.uri.toString()), "workspace");
      return;
    }

    if (this.isLinkedFolderFileUri(activeDocument.uri)) {
      this.renderSyncStatusItem(this.linkedFolderSyncState, "linked");
      return;
    }

    this.workspaceSyncItem.hide();
  }

  private renderSyncStatusItem(state: DocumentSyncState | undefined, source: "workspace" | "linked"): void {
    if (state === "pending") {
      this.workspaceSyncItem.text = "$(sync~spin) Sync pending";
      this.workspaceSyncItem.tooltip = source === "workspace"
        ? "MicroPython detected edits in this file and is waiting for auto-save to finish."
        : "MicroPython detected edits in this linked-folder file and is waiting for auto-save before syncing.";
      this.workspaceSyncItem.show();
      return;
    }

    if (state === "syncing") {
      this.workspaceSyncItem.text = "$(sync~spin) Syncing to device";
      this.workspaceSyncItem.tooltip = source === "workspace"
        ? "MicroPython is writing the latest saved change to the device."
        : "MicroPython is mirroring the linked folder to the device.";
      this.workspaceSyncItem.show();
      return;
    }

    if (state === "synced") {
      this.workspaceSyncItem.text = "$(cloud-upload) Synced to device";
      this.workspaceSyncItem.tooltip = source === "workspace"
        ? "The latest saved content was acknowledged by the MicroPython device."
        : "The latest saved linked-folder change was synced to the MicroPython device.";
      this.workspaceSyncItem.show();
      return;
    }

    if (state === "error") {
      this.workspaceSyncItem.text = "$(warning) Sync paused";
      this.workspaceSyncItem.tooltip = this.linkedFolderSyncError ?? "MicroPython linked folder sync is waiting to retry.";
      this.workspaceSyncItem.show();
      return;
    }

    this.workspaceSyncItem.hide();
  }

  private async restoreLinkedFolderSelection(): Promise<void> {
    const stored = this.context.globalState.get<string>(LINKED_SYNC_FOLDER_KEY);
    if (!stored) {
      return;
    }

    const resolved = path.resolve(stored);
    if (!await this.isDirectoryPath(resolved)) {
      await this.context.globalState.update(LINKED_SYNC_FOLDER_KEY, undefined);
      return;
    }

    await this.setLinkedFolderSelection(await this.buildSyncFolderSelection(resolved), false);
  }

  private async setLinkedFolderSelection(selection: SyncFolderSelection | undefined, persist = true): Promise<void> {
    if (this.linkedFolderSyncTimer) {
      clearTimeout(this.linkedFolderSyncTimer);
      this.linkedFolderSyncTimer = undefined;
    }

    this.linkedFolderSyncQueued = false;
    this.linkedFolderSyncInFlight = false;
    this.disposeLinkedFolderWatcher();
    this.linkedSyncSelection = selection;
    this.linkedFolderSyncError = undefined;
    this.linkedFolderSyncState = selection ? "synced" : undefined;

    if (selection) {
      this.activateLinkedFolderWatcher(selection);
    }
    if (persist) {
      await this.context.globalState.update(LINKED_SYNC_FOLDER_KEY, selection?.localFolder);
    }

    this.updateWorkspaceSyncStatus();
  }

  private disposeLinkedFolderWatcher(): void {
    if (!this.linkedFolderWatcher) {
      return;
    }

    this.linkedFolderWatcher.dispose();
    this.linkedFolderWatcher = undefined;
  }

  private activateLinkedFolderWatcher(selection: SyncFolderSelection): void {
    const rootUri = vscode.Uri.file(selection.localFolder);
    const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(rootUri, "**"), false, false, false);
    this.linkedFolderWatcher = vscode.Disposable.from(
      watcher,
      watcher.onDidCreate((uri: vscode.Uri) => {
        this.handleLinkedFolderFileEvent(uri);
      }),
      watcher.onDidChange((uri: vscode.Uri) => {
        this.handleLinkedFolderFileEvent(uri);
      }),
      watcher.onDidDelete((uri: vscode.Uri) => {
        this.handleLinkedFolderFileEvent(uri);
      }),
    );
  }

  private handleLinkedFolderFileEvent(uri: vscode.Uri): void {
    if (!this.isLinkedFolderFileUri(uri)) {
      return;
    }

    this.linkedFolderSyncState = "syncing";
    this.linkedFolderSyncError = undefined;
    this.updateWorkspaceSyncStatus();
    this.queueLinkedFolderSync();
  }

  private queueLinkedFolderSync(delayMs = LINKED_FOLDER_SYNC_DELAY_MS): void {
    if (!this.linkedSyncSelection) {
      return;
    }

    this.linkedFolderSyncQueued = true;
    if (this.linkedFolderSyncTimer) {
      clearTimeout(this.linkedFolderSyncTimer);
    }

    this.linkedFolderSyncTimer = setTimeout(() => {
      this.linkedFolderSyncTimer = undefined;
      void this.flushLinkedFolderSyncQueue();
    }, delayMs);
  }

  private async flushLinkedFolderSyncQueue(): Promise<void> {
    const selection = this.linkedSyncSelection;
    if (!selection || !this.linkedFolderSyncQueued) {
      return;
    }

    if (this.linkedFolderSyncInFlight || this.runInFlight || this.operationInFlight > 0 || this.sessionOpenInFlight) {
      this.queueLinkedFolderSync(LINKED_FOLDER_SYNC_RETRY_DELAY_MS);
      return;
    }

    if (!await this.isDirectoryPath(selection.localFolder)) {
      await this.setLinkedFolderSelection(undefined);
      void vscode.window.setStatusBarMessage("MicroPython linked folder no longer exists. Auto-sync was turned off.", 5000);
      return;
    }

    const port = this.selectedPort;
    if (!port) {
      this.handleLinkedFolderSyncFailure("Select a device to resume linked folder sync.", true);
      return;
    }

    const connected = await this.ensureSessionForPort(port, {
      force: true,
      notifyOnError: false,
      showTerminal: false,
    });
    if (!connected) {
      this.handleLinkedFolderSyncFailure(`Waiting for ${port} to reconnect.`, true);
      return;
    }

    this.linkedFolderSyncQueued = false;
    this.linkedFolderSyncInFlight = true;
    this.linkedFolderSyncState = "syncing";
    this.linkedFolderSyncError = undefined;
    this.updateWorkspaceSyncStatus();

    let result: SyncFolderResult;
    try {
      result = await this.executeFolderSyncWithSelection(port, selection, {
        title: `MicroPython: Syncing linked folder ${path.basename(selection.localFolder)}`,
        revealOutput: false,
        showProgress: false,
      });
    } catch (error) {
      this.linkedFolderSyncInFlight = false;
      this.handleLinkedFolderSyncFailure(this.errorMessage(error, "Linked folder sync failed."), true);
      return;
    }

    this.linkedFolderSyncInFlight = false;
    if (!result.ok) {
      this.handleLinkedFolderSyncFailure(result.error ?? "Linked folder sync failed.", true);
      return;
    }

    await this.afterFolderSyncSuccess(selection, false);
    this.linkedFolderSyncState = "synced";
    this.linkedFolderSyncError = undefined;
    this.updateWorkspaceSyncStatus();

    if (this.linkedFolderSyncQueued) {
      this.queueLinkedFolderSync();
    }
  }

  private handleLinkedFolderSyncFailure(message: string, retry: boolean): void {
    this.linkedFolderSyncState = "error";
    if (this.linkedFolderSyncError !== message) {
      this.linkedFolderSyncError = message;
      void vscode.window.setStatusBarMessage(`MicroPython linked folder sync paused: ${message}`, 5000);
    }
    this.updateWorkspaceSyncStatus();

    if (retry) {
      this.queueLinkedFolderSync(LINKED_FOLDER_SYNC_RETRY_DELAY_MS);
    }
  }

  private async loadSyncFolderHistory(): Promise<string[]> {
    const stored = this.context.globalState.get<string[]>(SYNC_FOLDER_HISTORY_KEY) ?? [];
    const existing: string[] = [];
    for (const candidate of stored) {
      const resolved = path.resolve(candidate);
      if (existing.includes(resolved)) {
        continue;
      }
      if (await this.isDirectoryPath(resolved)) {
        existing.push(resolved);
      }
    }
    const trimmed = existing.slice(0, MAX_SYNC_FOLDER_HISTORY);
    if (trimmed.length !== stored.length || trimmed.length !== existing.length) {
      await this.context.globalState.update(SYNC_FOLDER_HISTORY_KEY, trimmed);
    }
    return trimmed;
  }

  private async rememberSyncFolder(folderPath: string): Promise<void> {
    const resolved = path.resolve(folderPath);
    const history = await this.loadSyncFolderHistory();
    const next = [resolved, ...history.filter((entry) => entry !== resolved)].slice(0, MAX_SYNC_FOLDER_HISTORY);
    await this.context.globalState.update(SYNC_FOLDER_HISTORY_KEY, next);
  }

  private async buildSyncFolderSelection(folderPath: string): Promise<SyncFolderSelection> {
    const localFolder = path.resolve(folderPath);
    return {
      localFolder,
      remoteFolder: "/",
      deleteExtraneous: true,
    };
  }

  private isLinkedFolderFileUri(uri: vscode.Uri): boolean {
    if (uri.scheme !== "file" || !this.linkedSyncSelection) {
      return false;
    }

    return this.isPathWithinFolder(uri.fsPath, this.linkedSyncSelection.localFolder);
  }

  private isPathWithinFolder(targetPath: string, folderPath: string): boolean {
    const relativePath = path.relative(folderPath, targetPath);
    return relativePath === "" || (!relativePath.startsWith("..") && !path.isAbsolute(relativePath));
  }

  private async isFilePath(targetPath: string): Promise<boolean> {
    try {
      const stat = await fs.promises.stat(targetPath);
      return stat.isFile();
    } catch {
      return false;
    }
  }

  private async isDirectoryPath(targetPath: string): Promise<boolean> {
    try {
      const stat = await fs.promises.stat(targetPath);
      return stat.isDirectory();
    } catch {
      return false;
    }
  }

  private formatByteCount(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value >= 10 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
  }

  private shouldAutoConnectOnDetect(): boolean {
    return vscode.workspace.getConfiguration("micropython").get<boolean>("autoConnectOnDetect", false);
  }

  private shouldAutoScanWorkspace(): boolean {
    return vscode.workspace.getConfiguration("micropython").get<boolean>("autoScanWorkspace", false);
  }

  private async waitForOperationToSettle(timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + Math.max(0, timeoutMs);
    while (this.operationInFlight > 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return this.operationInFlight === 0;
  }

  private handleTerminalOutput(data: string): void {
    this.recentTerminalOutput = (this.recentTerminalOutput + data).slice(-256);
    if (this.terminalInteractionInFlight && this.terminalBufferHasFriendlyPrompt()) {
      this.terminalInteractionInFlight = false;
      this.refreshStatus();
    }
  }

  private terminalBufferHasFriendlyPrompt(): boolean {
    const normalized = this.recentTerminalOutput.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    return /(?:^|\n)(?:MicroPython >>>|>>>)[ \t]*$/.test(normalized);
  }

  private async resolveLocalFileForRun(): Promise<string | undefined> {
    const editor = vscode.window.activeTextEditor;

    if (editor && editor.document.uri.scheme === "file" && path.extname(editor.document.fileName).toLowerCase() === ".py") {
      if (editor.document.isDirty) {
        const saved = await editor.document.save();
        if (!saved) {
          throw new Error("File save cancelled. Run aborted.");
        }
      }
      return editor.document.fileName;
    }

    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: true,
      canSelectFolders: false,
      canSelectMany: false,
      openLabel: "Run on MicroPython",
      title: "MicroPython: Select Python File",
      filters: { Python: ["py"] },
    });

    if (!picked || picked.length === 0) {
      return undefined;
    }
    return picked[0].fsPath;
  }

  private async resolvePortForOperation(): Promise<string> {
    await this.pollDevices();

    if (this.devices.length === 0) {
      throw new Error("No device found. Connect MicroPython, then use Select Device.");
    }

    if (!this.selectedPort) {
      throw new Error("No device selected. Use MicroPython: Select Device first.");
    }

    const selected = this.devices.find((device) => device.port === this.selectedPort);
    if (selected) {
      return selected.port;
    }

    const missingSelection = this.selectedPort;
    await this.persistSelectedPort(undefined);
    throw new Error(`Selected device ${missingSelection} is not available. Use MicroPython: Select Device again.`);
  }

  private async pollDevices(options?: PollOptions): Promise<void> {
    if (!this.backendReady || this.pollInFlight || this.sessionOpenInFlight) {
      return;
    }

    const allowSessionConnect = options?.allowSessionConnect ?? this.shouldAutoConnectOnDetect();
    const operationActive = this.operationInFlight > 0 || this.runInFlight || this.terminalInteractionInFlight;
    this.pollInFlight = true;
    try {
      const result = await this.backend.scan();
      if (!result.ok) {
        this.devices = [];
        if (!this.sessionState.connected) {
          await this.persistSelectedPort(undefined);
        }
        this.refreshStatus();
        return;
      }

      this.devices = result.devices ?? [];
      const disconnectedPort = this.reconcileSelectedPort();
      if (disconnectedPort) {
        await this.handleSelectedPortDisconnected(disconnectedPort);
        return;
      }

      if (!this.selectedPort) {
        if (this.sessionState.connected) {
          await this.closeSessionSilently();
        }
        this.refreshStatus();
        return;
      }

      if (operationActive) {
        this.refreshStatus();
        return;
      }

      await this.closeDetachedSessionIfIdle();

      const shouldMaintainSession = this.shouldMaintainPersistentSession(Boolean(options?.showTerminalOnConnect));
      if (options?.forceSessionConnect || (allowSessionConnect && shouldMaintainSession && this.shouldAttemptSession(this.selectedPort))) {
        await this.ensureSessionForSelection({
          force: Boolean(options?.forceSessionConnect),
          notifyOnError: false,
          showTerminal: Boolean(options?.showTerminalOnConnect),
        });
      }

      this.refreshStatus();
    } catch {
      this.devices = [];
      this.refreshStatus();
    } finally {
      this.pollInFlight = false;
    }
  }

  private reconcileSelectedPort(): string | undefined {
    if (!this.selectedPort) {
      return undefined;
    }
    const stillAvailable = this.devices.some((device) => device.port === this.selectedPort);
    return stillAvailable ? undefined : this.selectedPort;
  }

  private async handleSelectedPortDisconnected(port: string): Promise<void> {
    if (this.disconnectHandlingInFlight) {
      return;
    }
    if (this.selectedPort !== port && this.sessionState.port !== port) {
      return;
    }

    this.disconnectHandlingInFlight = true;
    const message = `MicroPython device on ${port} disconnected.`;
    try {
      this.recentTerminalOutput = "";
      this.terminalInteractionInFlight = false;
      this.terminalInputQueue = Promise.resolve();

      try {
        await this.backend.abortSessionActivity("device-disconnected");
      } catch {
        // Best effort only.
      }

      this.sessionState = {
        connected: false,
        error: message,
        reason: "device-disconnected",
      };
      this.lastSessionError = message;
      await this.persistSelectedPort(undefined);
      this.refreshStatus();
      void vscode.window.showWarningMessage(message);
    } finally {
      this.disconnectHandlingInFlight = false;
    }
  }

  private async persistSelectedPort(port: string | undefined): Promise<void> {
    if (this.selectedPort === port) {
      return;
    }
    this.selectedPort = port;
    this.activeWorkspaceTarget = undefined;
    void this.clearWorkspaceFetchSession();
    await this.setWorkspaceClipboard(undefined);
    this.workspaceViewProvider.invalidate();
    await this.context.globalState.update(SELECTED_PORT_KEY, port);
    this.refreshStatus();
  }

  private async ensureSessionForPort(port: string, options: EnsureSessionOptions): Promise<boolean> {
    if (this.selectedPort !== port) {
      await this.persistSelectedPort(port);
    }
    return this.ensureSessionForSelection(options);
  }

  private async ensureSessionForSelection(options: EnsureSessionOptions): Promise<boolean> {
    if (!this.backendReady || !this.selectedPort) {
      return false;
    }

    const selectedAvailable = this.devices.some((device) => device.port === this.selectedPort);
    if (!selectedAvailable) {
      this.refreshStatus();
      return false;
    }

    if (this.sessionState.connected && this.sessionState.port === this.selectedPort) {
      if (options.showTerminal) {
        this.showReplTerminal(true);
      }
      return true;
    }

    if (this.sessionOpenInFlight) {
      const settled = await this.waitForSessionOpenToSettle(SESSION_OPEN_WAIT_MS);
      if (!settled) {
        this.refreshStatus();
        return false;
      }

      if (this.sessionState.connected && this.sessionState.port === this.selectedPort) {
        if (options.showTerminal) {
          this.showReplTerminal(true);
        }
        return true;
      }

      if (this.sessionOpenInFlight) {
        return false;
      }
    }

    this.sessionOpenInFlight = true;
    this.lastSessionAttemptAt = Date.now();
    this.lastSessionAttemptPort = this.selectedPort;
    if (options.showTerminal) {
      this.showReplTerminal(true);
    }
    this.refreshStatus();

    try {
      const result = await this.backend.openSession(this.selectedPort);
      this.sessionState = result;
      if (result.ok && result.connected && result.port === this.selectedPort) {
        this.lastSessionError = undefined;
        this.refreshStatus();
        return true;
      }

      const message = result.error ?? `Failed to open MicroPython session on ${this.selectedPort}.`;
      this.sessionState = { connected: false, error: message, reason: result.reason };
      this.lastSessionError = message;
      if (options.notifyOnError) {
        void vscode.window.showErrorMessage(message);
      }
      this.refreshStatus();
      return false;
    } catch (error) {
      const message = this.errorMessage(error, `Failed to open MicroPython session on ${this.selectedPort}.`);
      this.sessionState = { connected: false, error: message };
      this.lastSessionError = message;
      if (options.notifyOnError) {
        void vscode.window.showErrorMessage(message);
      }
      this.refreshStatus();
      return false;
    } finally {
      this.sessionOpenInFlight = false;
      this.refreshStatus();
    }
  }

  private shouldAttemptSession(port: string): boolean {
    if (this.sessionOpenInFlight) {
      return false;
    }
    if (this.sessionState.connected) {
      return this.sessionState.port !== port;
    }
    if (this.lastSessionAttemptPort !== port) {
      return true;
    }
    return Date.now() - this.lastSessionAttemptAt >= SESSION_RETRY_BACKOFF_MS;
  }

  private async waitForSessionOpenToSettle(timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + Math.max(0, timeoutMs);
    while (this.sessionOpenInFlight && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return !this.sessionOpenInFlight;
  }

  private async restartBackendAndReconnect(port: string): Promise<boolean> {
    try {
      await this.backend.restartService();
    } catch (error) {
      void vscode.window.showErrorMessage(this.errorMessage(error, "MicroPython backend restart failed."));
      return false;
    }

    this.sessionState = { connected: false };
    this.lastSessionError = undefined;
    this.refreshStatus();
    return this.ensureSessionForPort(port, { force: true, notifyOnError: false, showTerminal: true });
  }

  private async closeSessionSilently(reason = "idle-release"): Promise<void> {
    try {
      await this.backend.closeSession(reason);
    } catch {
      // Best effort only.
    }
    this.sessionState = { connected: false };
    this.terminalInteractionInFlight = false;
    this.refreshStatus();
  }

  private async closeDetachedSessionIfIdle(): Promise<void> {
    if (!this.sessionState.connected || this.shouldMaintainPersistentSession()) {
      return;
    }
    await this.closeSessionSilently();
  }

  private async handleReplTerminalClosed(): Promise<void> {
    if (this.operationInFlight > 0 || this.runInFlight || this.sessionOpenInFlight) {
      return;
    }
    await this.closeSessionSilently("terminal-closed");
  }

  private handleSessionStateChange(state: SessionState): void {
    const previousPort = this.sessionState.port ?? undefined;
    const previousConnected = this.sessionState.connected;
    this.sessionState = {
      connected: state.connected,
      port: state.port ?? undefined,
      error: state.error?.trim() || undefined,
      reason: state.reason?.trim() || undefined,
    };
    if (!this.sessionState.connected) {
      this.terminalInteractionInFlight = false;
    }
    if (this.sessionState.connected) {
      this.lastSessionError = undefined;
    } else if (this.sessionState.error) {
      this.lastSessionError = this.sessionState.error;
    }
    const portChanged = Boolean(previousPort && this.sessionState.port && previousPort !== this.sessionState.port);
    if (!this.sessionState.connected) {
      this.recentTerminalOutput = "";
    }
    const selectionCleared = !this.selectedPort;
    if (portChanged || selectionCleared) {
      this.activeWorkspaceTarget = undefined;
      void this.clearWorkspaceFetchSession();
      this.workspaceViewProvider.invalidate();
    }
    this.refreshStatus();

    if (!this.sessionState.connected && previousConnected && previousPort && this.selectedPort === previousPort && state.reason === "reader-failed") {
      void this.handleSelectedPortDisconnected(previousPort);
    }
  }

  private ensureReplTerminal(): vscode.Terminal {
    if (this.replTerminal) {
      return this.replTerminal;
    }

    const pty = new MicroPythonReplPseudoterminal(this.backend, async (data: string) => {
      await this.queueTerminalInput(data);
    });
    const terminal = vscode.window.createTerminal({
      name: "MicroPython",
      iconPath: new vscode.ThemeIcon("chip"),
      pty,
    });

    this.replPty = pty;
    this.replTerminal = terminal;
    this.context.subscriptions.push(pty, terminal);
    return terminal;
  }

  private showReplTerminal(preserveFocus: boolean): void {
    const terminal = this.ensureReplTerminal();
    terminal.show(preserveFocus);
  }

  private shouldMaintainPersistentSession(showTerminalOnConnect = false): boolean {
    return showTerminalOnConnect
      || this.terminalInteractionInFlight;
  }

  private refreshStatus(): void {
    if (!this.backendReady) {
      this.setInitializingStatus();
      return;
    }

    if (this.selectedPort && this.sessionState.connected && this.sessionState.port === this.selectedPort) {
      this.setConnectedStatus(this.selectedPort);
      return;
    }

    if (!this.selectedPort) {
      if (this.devices.length > 0) {
        this.setNeedsSelectionStatus("Device detected. Run Select Device to enable communication.");
        return;
      }

      this.setNoDeviceStatus("No device selected.");
      return;
    }

    const selectedAvailable = this.devices.some((device) => device.port === this.selectedPort);
    if (!selectedAvailable) {
      this.setNoDeviceStatus(`Selected device ${this.selectedPort} is not available.`);
      return;
    }

    if (this.sessionOpenInFlight || this.operationInFlight > 0) {
      this.setConnectingStatus(this.selectedPort);
      return;
    }

    const reason = this.lastSessionError ?? "Selected device is available. MicroPython opens the serial session on demand.";
    this.setSelectedStatus(this.selectedPort, reason);
  }

  private setInitializingStatus(): void {
    this.statusItem.text = "$(sync~spin) MicroPython: initializing";
    this.statusItem.color = undefined;
    this.statusItem.tooltip = "Preparing MicroPython runtime";
    this.setRunVisible(false);
  }

  private setConnectedStatus(port: string): void {
    this.statusItem.text = `$(plug) Connected: ${port}`;
    this.statusItem.color = new vscode.ThemeColor("terminal.ansiGreen");
    this.statusItem.tooltip = `MicroPython session active on ${port}`;
    this.setRunVisible(true);
  }

  private setConnectingStatus(port: string): void {
    this.statusItem.text = `$(sync~spin) Connecting: ${port}`;
    this.statusItem.color = undefined;
    this.statusItem.tooltip = `Opening MicroPython session on ${port}`;
    this.setRunVisible(true);
  }

  private setSelectedStatus(port: string, reason: string): void {
    this.statusItem.text = `$(plug) Selected: ${port}`;
    this.statusItem.color = new vscode.ThemeColor("terminal.ansiYellow");
    this.statusItem.tooltip = reason;
    this.setRunVisible(true);
  }

  private setNeedsSelectionStatus(reason: string): void {
    this.statusItem.text = "$(plug) Select Device";
    this.statusItem.color = new vscode.ThemeColor("terminal.ansiYellow");
    this.statusItem.tooltip = reason;
    this.setRunVisible(false);
  }

  private setNoDeviceStatus(reason: string): void {
    this.statusItem.text = "$(circle-slash) No device";
    this.statusItem.color = new vscode.ThemeColor("terminal.ansiRed");
    this.statusItem.tooltip = reason;
    this.setRunVisible(false);
  }

  private setRunVisible(visible: boolean): void {
    if (visible) {
      this.runItem.show();
      this.runInteractiveItem.show();
      return;
    }
    this.runItem.hide();
    this.runInteractiveItem.hide();
  }

  private setRunButtonBusy(busy: boolean): void {
    if (busy) {
      this.runItem.text = "$(sync~spin) Running Non-Interactive";
      this.runItem.tooltip = "MicroPython non-interactive run in progress";
      this.runItem.command = undefined;
      this.runInteractiveItem.command = undefined;
      return;
    }
    this.runItem.text = "$(play) Run Non-Interactive";
    this.runItem.tooltip = "Run active Python file on MicroPython through raw REPL";
    this.runItem.command = "micropython.runCurrentFile";
    this.runInteractiveItem.command = "micropython.runInteractiveFile";
    this.runInteractiveItem.text = "$(terminal) Run Interactive";
    this.runInteractiveItem.tooltip = "Run active Python file on MicroPython through the normal REPL";
  }

  private errorMessage(error: unknown, fallback: string): string {
    if (error instanceof Error && error.message.trim().length > 0) {
      return error.message;
    }
    return fallback;
  }
}
