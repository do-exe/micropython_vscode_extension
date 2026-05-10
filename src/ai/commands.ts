// AI-invokable commands for MicroPython device interaction.
// These commands accept programmatic arguments, while also prompting when
// launched manually from the VS Code command palette.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawn } from "child_process";
import * as vscode from "vscode";
import type { BackendServiceClient } from "../backend/backendServiceClient";
import type { MicroPythonExtensionController } from "../controller/extensionController";

type MicroPythonRunAndTestInput = {
  port?: string;
  localFile?: string;
  code?: string;
  projectFolder?: string;
  remoteRoot?: string;
  syncProject?: boolean;
  deleteExtraneous?: boolean;
  timeoutSeconds?: number;
};

type MicroPythonSyncProjectInput = {
  port?: string;
  projectFolder?: string;
  remoteRoot?: string;
  deleteExtraneous?: boolean;
};

type MicroPythonDeviceStatusInput = {
  port?: string;
};

type StringInputOptions = {
  title: string;
  prompt: string;
  placeHolder?: string;
  trim?: boolean;
  allowEmpty?: boolean;
};

type StringInputResult = {
  value: string;
  interactive: boolean;
};

type BackendOkResult = {
  ok?: boolean;
  error?: string;
};

type AgentProcessResult = {
  code: number | null;
  stdout: string;
  stderr: string;
};

type AgentMcpLaunchConfig = {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  serverPath: string;
};

export class AICommands {
  private readonly output = vscode.window.createOutputChannel("MicroPython AI");
  private readonly agentStatusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 91);
  private languageModelToolsRegistered = false;
  private mcpProviderRegistered = false;
  private extensionPath: string | undefined;

  constructor(
    private backendClient: BackendServiceClient,
    private controller: MicroPythonExtensionController
  ) {}

  private getSelectedPort(): string {
    const port = this.controller.getSelectedPort();
    if (!port) {
      throw new Error("No MicroPython device selected. Please select a device first.");
    }
    return port;
  }

  public registerCommands(context: vscode.ExtensionContext): void {
    this.extensionPath = context.extensionPath;
    context.subscriptions.push(this.output, this.agentStatusItem);
    this.agentStatusItem.command = "micropython.ai.showAgentMcpStatus";
    this.agentStatusItem.text = "$(tools) MicroPython AI";
    this.agentStatusItem.tooltip = "Show MicroPython AI agent and MCP access status";
    this.agentStatusItem.show();

    this.registerLanguageModelTools(context);
    this.registerMcpServerProvider(context);

    context.subscriptions.push(
      vscode.commands.registerCommand("micropython.ai.showAgentMcpStatus", async () => {
        await this.showAgentMcpStatusCommand();
      }),
      vscode.commands.registerCommand("micropython.ai.configureAgentMcp", async () => {
        await this.configureAgentMcpCommand();
      }),
      vscode.commands.registerCommand("micropython.ai.runCode", async (input?: unknown) => {
        const code = await this.requireStringInput(input, input, ["code"], {
          title: "MicroPython AI Run Code",
          prompt: "MicroPython code to run on the selected device",
          placeHolder: "print('hello from MicroPython')",
          trim: false,
        });
        const port = this.getSelectedPort();
        const tempFile = path.join(os.tmpdir(), `micropython_ai_${Date.now()}.py`);

        await fs.promises.writeFile(tempFile, code.value, "utf8");
        try {
          const result = this.ensureOk(await this.backendClient.runFileInteractive(port, tempFile));
          return this.completeCommand("Run Code", result, code.interactive);
        } finally {
          await fs.promises.unlink(tempFile).catch(() => undefined);
        }
      }),

      vscode.commands.registerCommand("micropython.ai.uploadFile", async (inputOrLocalPath?: unknown, remotePathInput?: unknown) => {
        const localPath = await this.requireStringInput(inputOrLocalPath, inputOrLocalPath, ["localPath"], {
          title: "MicroPython AI Upload File",
          prompt: "Local file path to upload",
          placeHolder: "/home/user/project/main.py",
        });
        const remotePath = await this.requireStringInput(remotePathInput, inputOrLocalPath, ["remotePath"], {
          title: "MicroPython AI Upload File",
          prompt: "Device destination path",
          placeHolder: "/main.py",
        });
        const port = this.getSelectedPort();
        const content = await vscode.workspace.fs.readFile(vscode.Uri.file(localPath.value));
        const contentBase64 = Buffer.from(content).toString("base64");
        const result = this.ensureOk(await this.backendClient.writeWorkspaceFile(port, remotePath.value, contentBase64, {
          create: true,
          overwrite: true,
        }));
        return this.completeCommand("Upload File", result, localPath.interactive || remotePath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.downloadFile", async (inputOrRemotePath?: unknown, localPathInput?: unknown) => {
        const remotePath = await this.requireStringInput(inputOrRemotePath, inputOrRemotePath, ["remotePath"], {
          title: "MicroPython AI Download File",
          prompt: "Device source path",
          placeHolder: "/boot.py",
        });
        const localPath = await this.requireStringInput(localPathInput, inputOrRemotePath, ["localPath"], {
          title: "MicroPython AI Download File",
          prompt: "Local destination path",
          placeHolder: "/tmp/boot.py",
        });
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.readWorkspaceFile(port, remotePath.value));
        if (!result.contentBase64) {
          throw new Error(result.error ?? "File content not available.");
        }
        const content = Buffer.from(result.contentBase64, "base64");
        await vscode.workspace.fs.writeFile(vscode.Uri.file(localPath.value), content);
        return this.completeCommand("Download File", {
          ok: true,
          remotePath: remotePath.value,
          localPath: localPath.value,
          bytes: content.length,
        }, remotePath.interactive || localPath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.listFiles", async (input?: unknown) => {
        const port = this.getSelectedPort();
        const remotePath = this.optionalStringInput(input, input, ["remotePath"]) ?? "/";
        const result = this.ensureOk(await this.backendClient.listWorkspaceDirectory(port, remotePath));
        return this.completeCommand("List Files", result.entries ?? [], input === undefined);
      }),

      vscode.commands.registerCommand("micropython.ai.createDir", async (input?: unknown) => {
        const remotePath = await this.requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Create Directory",
          prompt: "Device directory path to create",
          placeHolder: "/lib",
        });
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.createWorkspaceDirectory(port, remotePath.value));
        return this.completeCommand("Create Directory", result, remotePath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.delete", async (input?: unknown) => {
        const remotePath = await this.requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Delete",
          prompt: "Device file or directory path to delete",
          placeHolder: "/old.py",
        });
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.deleteWorkspaceEntry(port, remotePath.value, true));
        return this.completeCommand("Delete", result, remotePath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.readFile", async (input?: unknown) => {
        const remotePath = await this.requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Read File",
          prompt: "Device file path to read",
          placeHolder: "/boot.py",
        });
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.readWorkspaceFile(port, remotePath.value));
        if (!result.contentBase64) {
          throw new Error(result.error ?? "File content not available.");
        }
        const content = Buffer.from(result.contentBase64, "base64").toString("utf8");
        return this.completeCommand("Read File", content, remotePath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.writeFile", async (inputOrRemotePath?: unknown, contentInput?: unknown) => {
        const remotePath = await this.requireStringInput(inputOrRemotePath, inputOrRemotePath, ["remotePath"], {
          title: "MicroPython AI Write File",
          prompt: "Device file path to write",
          placeHolder: "/main.py",
        });
        const content = await this.requireStringInput(contentInput, inputOrRemotePath, ["content"], {
          title: "MicroPython AI Write File",
          prompt: "File content",
          placeHolder: "print('hello from MicroPython')",
          trim: false,
          allowEmpty: true,
        });
        const port = this.getSelectedPort();
        const contentBase64 = Buffer.from(content.value, "utf8").toString("base64");
        const result = this.ensureOk(await this.backendClient.writeWorkspaceFile(port, remotePath.value, contentBase64, {
          create: true,
          overwrite: true,
        }));
        return this.completeCommand("Write File", result, remotePath.interactive || content.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.stat", async (input?: unknown) => {
        const remotePath = await this.requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI File Stats",
          prompt: "Device file or directory path",
          placeHolder: "/boot.py",
        });
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.statWorkspaceEntry(port, remotePath.value));
        return this.completeCommand("File Stats", result, remotePath.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.sendRepl", async (input?: unknown) => {
        const command = await this.requireStringInput(input, input, ["command"], {
          title: "MicroPython AI Send REPL Command",
          prompt: "REPL command to send",
          placeHolder: "x = 42",
        });
        this.getSelectedPort();
        await this.backendClient.sendTerminalInput(command.value + "\n");
        return this.completeCommand("Send REPL Command", { ok: true }, command.interactive);
      }),

      vscode.commands.registerCommand("micropython.ai.softReset", async () => {
        const port = this.getSelectedPort();
        const result = this.ensureOk(await this.backendClient.softReset(port, 30));
        return this.completeCommand("Soft Reset", result, true);
      })
    );
  }

  private registerLanguageModelTools(context: vscode.ExtensionContext): void {
    const lm = vscode.lm;
    if (!lm || typeof lm.registerTool !== "function") {
      this.refreshAgentStatusIndicator();
      return;
    }

    context.subscriptions.push(
      lm.registerTool<MicroPythonDeviceStatusInput>("micropython_device_status", {
        invoke: async (options) => this.createToolResult(await this.getDeviceStatus(options.input ?? {})),
        prepareInvocation: () => ({
          invocationMessage: "Checking MicroPython device status",
        }),
      }),
      lm.registerTool<MicroPythonSyncProjectInput>("micropython_sync_project", {
        invoke: async (options) => this.createToolResult(await this.syncProjectForAgent(options.input ?? {})),
        prepareInvocation: (options) => ({
          invocationMessage: `Syncing MicroPython project${options.input?.projectFolder ? `: ${options.input.projectFolder}` : ""}`,
        }),
      }),
      lm.registerTool<MicroPythonRunAndTestInput>("micropython_run_and_test", {
        invoke: async (options, token) => this.createToolResult(await this.runAndTestForAgent(options.input ?? {}, token)),
        prepareInvocation: (options) => ({
          invocationMessage: `Running MicroPython code${options.input?.localFile ? `: ${path.basename(options.input.localFile)}` : ""}`,
        }),
      }),
    );
    this.languageModelToolsRegistered = true;
    this.refreshAgentStatusIndicator();
  }

  private registerMcpServerProvider(context: vscode.ExtensionContext): void {
    const lm = vscode.lm;
    if (!lm || typeof lm.registerMcpServerDefinitionProvider !== "function" || typeof vscode.McpStdioServerDefinition !== "function") {
      this.refreshAgentStatusIndicator();
      return;
    }

    context.subscriptions.push(lm.registerMcpServerDefinitionProvider("micropython.mcpServerProvider", {
      provideMcpServerDefinitions: async () => {
        try {
          const launch = await this.backendClient.getBundledPythonLaunch();
          const server = new vscode.McpStdioServerDefinition(
            "MicroPython",
            launch.pythonPath,
            [path.join(context.extensionPath, "backend", "mcp_server.py")],
            this.toMcpEnvironment(launch.env),
            String(context.extension.packageJSON?.version ?? "0.0.0"),
          );
          server.cwd = vscode.Uri.file(context.extensionPath);
          return [server];
        } catch {
          return [];
        }
      },
    }));
    this.mcpProviderRegistered = true;
    this.refreshAgentStatusIndicator();
  }

  private async showAgentMcpStatusCommand(): Promise<void> {
    const status = await this.getAgentMcpStatus();
    this.output.appendLine(`[${new Date().toISOString()}] Agent MCP Status`);
    this.output.appendLine(this.serializeResult(status));
    this.output.appendLine("");
    this.output.show(false);

    const codexConfigured = Boolean(status.codexGlobalMcp.configured);
    const vscodeConfigured = Boolean(status.vscodeWorkspaceMcp.configured);
    const summary = [
      `VS Code tools: ${this.languageModelToolsRegistered ? "registered" : "not available"}`,
      `VS Code MCP: ${this.mcpProviderRegistered ? "published" : "not available"}`,
      `Workspace mcp.json: ${vscodeConfigured ? "configured" : "not configured"}`,
      `Codex MCP: ${codexConfigured ? "configured" : "not configured"}`,
    ].join(" | ");
    void vscode.window.showInformationMessage(summary, "Configure MCP Access").then((choice) => {
      if (choice === "Configure MCP Access") {
        void this.configureAgentMcpCommand();
      }
    });
  }

  private async configureAgentMcpCommand(): Promise<void> {
    const choice = await vscode.window.showQuickPick([
      {
        label: "Configure VS Code workspace MCP",
        description: "Writes .vscode/mcp.json for VS Code agent runtimes.",
        target: "vscode",
      },
      {
        label: "Configure Codex global MCP",
        description: "Runs codex mcp add so Codex sessions receive MicroPython tools.",
        target: "codex",
      },
      {
        label: "Configure both",
        description: "Syncs VS Code workspace MCP and Codex global MCP.",
        target: "both",
      },
    ] as const, {
      title: "MicroPython: Configure AI Agent MCP Access",
      placeHolder: "Choose which agent MCP configuration to sync",
      ignoreFocusOut: true,
    });
    if (!choice) {
      return;
    }

    const launch = await this.getAgentMcpLaunchConfig();
    const results: unknown[] = [];

    if (choice.target === "vscode" || choice.target === "both") {
      results.push(await this.writeWorkspaceMcpConfig(launch));
    }

    if (choice.target === "codex" || choice.target === "both") {
      const confirmation = await vscode.window.showWarningMessage(
        "Configure Codex global MCP access for MicroPython? This updates your Codex MCP configuration so new Codex sessions can see the MicroPython tools.",
        { modal: true },
        "Configure Codex",
      );
      if (confirmation === "Configure Codex") {
        results.push(await this.configureCodexMcp(launch));
      }
    }

    this.refreshAgentStatusIndicator();
    this.output.appendLine(`[${new Date().toISOString()}] Configure Agent MCP Access`);
    this.output.appendLine(this.serializeResult(results));
    this.output.appendLine("");
    this.output.show(false);
    void vscode.window.showInformationMessage("MicroPython agent MCP access configuration complete. Restart or refresh any already-open agent sessions.");
  }

  private async getAgentMcpStatus(): Promise<{
    ok: boolean;
    server: string;
    vscodeLanguageModelTools: { available: boolean; registered: boolean };
    vscodeExtensionMcpProvider: { available: boolean; registered: boolean };
    vscodeWorkspaceMcp: { configured: boolean; path: string; error?: string };
    codexGlobalMcp: { codexCliAvailable: boolean; configured: boolean; error?: string };
    guidance: string[];
  }> {
    const workspaceMcpPath = this.getWorkspaceMcpConfigPath();
    let workspaceConfigured = false;
    let workspaceError: string | undefined;
    try {
      workspaceConfigured = workspaceMcpPath ? await this.workspaceMcpConfigHasMicroPython(workspaceMcpPath) : false;
    } catch (error) {
      workspaceError = this.errorText(error);
    }

    const codexStatus = await this.getCodexMcpStatus();
    return {
      ok: true,
      server: "micropython",
      vscodeLanguageModelTools: {
        available: Boolean(vscode.lm && typeof vscode.lm.registerTool === "function"),
        registered: this.languageModelToolsRegistered,
      },
      vscodeExtensionMcpProvider: {
        available: Boolean(vscode.lm && typeof vscode.lm.registerMcpServerDefinitionProvider === "function"),
        registered: this.mcpProviderRegistered,
      },
      vscodeWorkspaceMcp: {
        configured: workspaceConfigured,
        path: workspaceMcpPath ?? "No file workspace is open.",
        error: workspaceError,
      },
      codexGlobalMcp: codexStatus,
      guidance: [
        "Copilot/VS Code Chat can use native Language Model Tools when the chat runtime passes extension tools through.",
        "VS Code MCP servers shown in Agent Customizations are not automatically inherited by every third-party agent runtime.",
        "Use MicroPython: Configure AI Agent MCP Access to write .vscode/mcp.json and/or run codex mcp add for Codex.",
        "Restart or refresh already-open agent sessions after changing MCP configuration.",
      ],
    };
  }

  public async getDeviceStatus(input: MicroPythonDeviceStatusInput = {}): Promise<unknown> {
    const scan = this.ensureOk(await this.backendClient.scan());
    const selectedPort = input.port?.trim() || this.controller.getSelectedPort();
    const devices = scan.devices ?? [];
    const selectedDevice = selectedPort
      ? devices.find((device) => device.port === selectedPort)
      : undefined;
    return this.completeCommand("Device Status", {
      ok: true,
      selectedPort: selectedPort ?? null,
      selectedDevice: selectedDevice ?? null,
      devices,
      guidance: selectedPort || devices.length === 1
        ? "Use micropython_run_and_test for upload/run/test workflows. Do not use mpremote, ampy, esptool, or raw serial directly unless this tool reports unsupported."
        : "Ask the user to select a MicroPython device, or pass a port explicitly if the intended device is known.",
    }, false);
  }

  public async syncProjectForAgent(input: MicroPythonSyncProjectInput = {}): Promise<unknown> {
    const port = await this.resolveAgentPort(input.port);
    const projectFolder = this.resolveProjectFolder(input.projectFolder, undefined);
    if (!projectFolder) {
      throw new Error("No project folder was provided, and no file workspace is open.");
    }
    const remoteRoot = this.normalizeRemoteRoot(input.remoteRoot);
    const deleteExtraneous = input.deleteExtraneous === true;
    const progressLines: string[] = [];

    const result = this.ensureOk(await this.backendClient.syncFolder(
      port,
      projectFolder,
      remoteRoot,
      deleteExtraneous,
      (line: string, isError: boolean) => {
        progressLines.push(isError ? `[ERROR] ${line}` : line);
      },
    ));

    return this.completeCommand("Sync Project", {
      ok: true,
      port,
      projectFolder,
      remoteRoot,
      deleteExtraneous,
      result,
      progress: progressLines.slice(-80),
      guidance: "Project sync used the extension backend. Do not retry with mpremote, ampy, esptool, or raw serial unless this result says unsupported.",
    }, false);
  }

  public async runAndTestForAgent(
    input: MicroPythonRunAndTestInput = {},
    token?: vscode.CancellationToken,
  ): Promise<unknown> {
    const port = await this.resolveAgentPort(input.port);
    const workspaceFolder = this.resolveProjectFolder(input.projectFolder, input.localFile);
    const remoteRoot = this.normalizeRemoteRoot(input.remoteRoot);
    const timeoutSeconds = this.normalizeTimeout(input.timeoutSeconds);
    const syncProject = input.syncProject ?? Boolean(workspaceFolder && !input.code);
    const deleteExtraneous = input.deleteExtraneous === true;
    const steps: unknown[] = [];
    const startedAt = Date.now();

    if (syncProject && workspaceFolder) {
      const progressLines: string[] = [];
      const syncResult = await this.backendClient.syncFolder(
        port,
        workspaceFolder,
        remoteRoot,
        deleteExtraneous,
        (line: string, isError: boolean) => {
          progressLines.push(isError ? `[ERROR] ${line}` : line);
        },
      );
      steps.push({
        step: "syncProject",
        ok: syncResult.ok,
        projectFolder: workspaceFolder,
        remoteRoot,
        deleteExtraneous,
        result: syncResult,
        progress: progressLines.slice(-80),
      });
      if (!syncResult.ok) {
        return this.completeCommand("Run And Test", {
          ok: false,
          port,
          failedStep: "syncProject",
          error: syncResult.error ?? "MicroPython project sync failed.",
          steps,
        }, false);
      }
    }

    let tempFile: string | undefined;
    const localFile = await this.resolveRunFile(input, workspaceFolder);
    let runFile = localFile;
    if (input.code !== undefined) {
      tempFile = path.join(os.tmpdir(), `micropython_agent_${Date.now()}.py`);
      await fs.promises.writeFile(tempFile, input.code, "utf8");
      runFile = tempFile;
    }

    if (!runFile) {
      return this.completeCommand("Run And Test", {
        ok: false,
        port,
        failedStep: "resolveRunFile",
        error: "No MicroPython file or code was provided, and no active/workspace main.py file was found.",
        steps,
      }, false);
    }

    const outputLines: string[] = [];
    const fallbackCancellation = token ? undefined : new vscode.CancellationTokenSource();
    try {
      const runResult = await this.backendClient.runFileStreaming(
        port,
        runFile,
        timeoutSeconds,
        (line: string, isError: boolean) => {
          outputLines.push(isError ? `[ERROR] ${line}` : line);
        },
        token ?? fallbackCancellation!.token,
      );
      steps.push({
        step: "run",
        ok: runResult.ok,
        localFile,
        usedInlineCode: input.code !== undefined,
        result: runResult,
        streamedOutput: outputLines,
      });

      return this.completeCommand("Run And Test", {
        ok: runResult.ok,
        port,
        localFile,
        usedInlineCode: input.code !== undefined,
        syncedProject: syncProject,
        durationMs: Date.now() - startedAt,
        stdout: runResult.output,
        error: runResult.error,
        steps,
        nextAction: runResult.ok
          ? "The MicroPython run completed. Inspect stdout for test assertions or device output."
          : "Fix the reported MicroPython error, then call micropython_run_and_test again. Do not switch to mpremote, ampy, esptool, or raw serial.",
      }, false);
    } finally {
      fallbackCancellation?.dispose();
      if (tempFile) {
        await fs.promises.unlink(tempFile).catch(() => undefined);
      }
    }
  }

  private ensureOk<T extends BackendOkResult>(result: T): T {
    if (result.ok === false) {
      throw new Error(result.error ?? "MicroPython operation failed.");
    }
    return result;
  }

  private completeCommand<T>(label: string, result: T, interactive: boolean): T {
    this.output.appendLine(`[${new Date().toISOString()}] ${label}`);
    this.output.appendLine(this.serializeResult(result));
    this.output.appendLine("");

    if (interactive) {
      this.output.show(true);
      void vscode.window.showInformationMessage(`MicroPython AI ${label} complete. See the MicroPython AI output.`);
    }

    return result;
  }

  private serializeResult(result: unknown): string {
    if (typeof result === "string") {
      return result;
    }
    if (result === undefined) {
      return "undefined";
    }
    return JSON.stringify(result, null, 2) ?? String(result);
  }

  private createToolResult(result: unknown): vscode.LanguageModelToolResult {
    return new vscode.LanguageModelToolResult([
      new vscode.LanguageModelTextPart(this.serializeResult(result)),
    ]);
  }

  private async resolveAgentPort(explicitPort?: string): Promise<string> {
    const requestedPort = explicitPort?.trim();
    if (requestedPort) {
      return requestedPort;
    }

    const selectedPort = this.controller.getSelectedPort();
    if (selectedPort) {
      return selectedPort;
    }

    const scan = this.ensureOk(await this.backendClient.scan());
    const devices = scan.devices ?? [];
    if (devices.length === 1) {
      return devices[0].port;
    }
    if (devices.length > 1) {
      throw new Error(`Multiple MicroPython devices detected (${devices.map((device) => device.port).join(", ")}). Pass the intended port or ask the user to select a device.`);
    }
    throw new Error("No MicroPython device detected. Connect a device, then try again.");
  }

  private resolveProjectFolder(inputFolder: string | undefined, localFile: string | undefined): string | undefined {
    if (inputFolder?.trim()) {
      return path.resolve(inputFolder.trim());
    }
    if (localFile?.trim()) {
      return path.dirname(path.resolve(localFile.trim()));
    }

    const activeFile = vscode.window.activeTextEditor?.document.uri;
    if (activeFile?.scheme === "file") {
      const folder = vscode.workspace.getWorkspaceFolder(activeFile);
      if (folder?.uri.scheme === "file") {
        return folder.uri.fsPath;
      }
      return path.dirname(activeFile.fsPath);
    }

    const workspaceFolder = vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file");
    return workspaceFolder?.uri.fsPath;
  }

  private async resolveRunFile(input: MicroPythonRunAndTestInput, projectFolder: string | undefined): Promise<string | undefined> {
    if (input.localFile?.trim()) {
      return path.resolve(input.localFile.trim());
    }

    const activeDocument = vscode.window.activeTextEditor?.document;
    if (activeDocument?.uri.scheme === "file" && activeDocument.uri.fsPath.endsWith(".py")) {
      if (activeDocument.isDirty) {
        await activeDocument.save();
      }
      return activeDocument.uri.fsPath;
    }

    if (projectFolder) {
      const mainFile = path.join(projectFolder, "main.py");
      try {
        const stat = await fs.promises.stat(mainFile);
        if (stat.isFile()) {
          return mainFile;
        }
      } catch {
        // main.py is optional; callers may pass localFile or code instead.
      }
    }

    return undefined;
  }

  private normalizeRemoteRoot(remoteRoot: string | undefined): string {
    const text = remoteRoot?.trim() || "/";
    const normalized = path.posix.normalize(text.replace(/\\/g, "/"));
    if (normalized === "." || normalized === "") {
      return "/";
    }
    return normalized.startsWith("/") ? normalized : `/${normalized}`;
  }

  private normalizeTimeout(timeoutSeconds: number | undefined): number {
    if (typeof timeoutSeconds !== "number" || !Number.isFinite(timeoutSeconds)) {
      return vscode.workspace.getConfiguration("micropython").get<number>("runTimeoutSeconds", 0);
    }
    return Math.max(0, Math.min(600, timeoutSeconds));
  }

  private toMcpEnvironment(env: NodeJS.ProcessEnv): Record<string, string | number | null> {
    const normalized: Record<string, string | number | null> = {};
    for (const [key, value] of Object.entries(env)) {
      if (value !== undefined) {
        normalized[key] = value;
      }
    }
    return normalized;
  }

  private refreshAgentStatusIndicator(): void {
    const hasNativeTools = this.languageModelToolsRegistered;
    const hasProvider = this.mcpProviderRegistered;
    if (hasNativeTools && hasProvider) {
      this.agentStatusItem.text = "$(tools) MicroPython AI";
      this.agentStatusItem.tooltip = "MicroPython AI tools and MCP provider are registered. Click for per-agent access status.";
      return;
    }
    if (hasNativeTools || hasProvider) {
      this.agentStatusItem.text = "$(warning) MicroPython AI";
      this.agentStatusItem.tooltip = "MicroPython AI is partially available. Click for per-agent access status.";
      return;
    }
    this.agentStatusItem.text = "$(error) MicroPython AI";
    this.agentStatusItem.tooltip = "MicroPython AI tools are not available in this VS Code host. Click for details.";
  }

  private async getAgentMcpLaunchConfig(): Promise<AgentMcpLaunchConfig> {
    const launch = await this.backendClient.getBundledPythonLaunch();
    const serverPath = path.join(this.extensionPath ?? this.controllerExtensionPath(), "backend", "mcp_server.py");
    return {
      name: "micropython",
      command: launch.pythonPath,
      args: [serverPath],
      env: this.minimalMcpEnvironment(launch.env),
      serverPath,
    };
  }

  private controllerExtensionPath(): string {
    const extension = vscode.extensions.getExtension("do-exe.micropython-vscode-extension");
    if (extension) {
      return extension.extensionPath;
    }

    const extensionFromAnyPackageName = vscode.extensions.all.find((candidate) => {
      const packageJson = candidate.packageJSON as Partial<{ name: string; publisher: string }>;
      return packageJson.name === "micropython-vscode-extension";
    });
    if (extensionFromAnyPackageName) {
      return extensionFromAnyPackageName.extensionPath;
    }

    return path.resolve(__dirname, "..", "..");
  }

  private minimalMcpEnvironment(env: NodeJS.ProcessEnv): Record<string, string> {
    const keys = ["PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "LD_LIBRARY_PATH", "PATH"];
    const normalized: Record<string, string> = {};
    for (const key of keys) {
      const value = env[key];
      if (value) {
        normalized[key] = value;
      }
    }
    return normalized;
  }

  private getWorkspaceMcpConfigPath(): string | undefined {
    const workspaceFolder = vscode.workspace.workspaceFolders?.find((folder) => folder.uri.scheme === "file");
    if (!workspaceFolder) {
      return undefined;
    }
    return path.join(workspaceFolder.uri.fsPath, ".vscode", "mcp.json");
  }

  private async workspaceMcpConfigHasMicroPython(configPath: string): Promise<boolean> {
    let raw: string;
    try {
      raw = await fs.promises.readFile(configPath, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return false;
      }
      throw error;
    }
    const parsed = JSON.parse(raw) as Partial<{
      servers: Record<string, unknown>;
      mcpServers: Record<string, unknown>;
    }>;
    return Boolean(parsed.servers?.micropython || parsed.mcpServers?.micropython);
  }

  private async writeWorkspaceMcpConfig(launch: AgentMcpLaunchConfig): Promise<unknown> {
    const configPath = this.getWorkspaceMcpConfigPath();
    if (!configPath) {
      throw new Error("Open a file workspace before writing .vscode/mcp.json.");
    }

    let config: Record<string, unknown> = {};
    try {
      const raw = await fs.promises.readFile(configPath, "utf8");
      config = JSON.parse(raw) as Record<string, unknown>;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        throw error;
      }
    }

    const servers = this.asRecord(config.servers) ?? {};
    servers[launch.name] = {
      type: "stdio",
      command: launch.command,
      args: launch.args,
      env: launch.env,
    };
    config.servers = servers;

    await fs.promises.mkdir(path.dirname(configPath), { recursive: true });
    await fs.promises.writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");

    return {
      ok: true,
      agent: "VS Code workspace MCP",
      path: configPath,
      server: launch.name,
      note: "VS Code may ask you to trust or restart this MCP server before tools appear.",
    };
  }

  private async configureCodexMcp(launch: AgentMcpLaunchConfig): Promise<unknown> {
    const existing = await this.runAgentProcess("codex", ["mcp", "get", launch.name], 5000);
    if (existing.code === 0) {
      const removed = await this.runAgentProcess("codex", ["mcp", "remove", launch.name], 10000);
      if (removed.code !== 0) {
        throw new Error(`Failed to remove existing Codex MCP server: ${removed.stderr || removed.stdout}`);
      }
    }

    const args = ["mcp", "add"];
    for (const [key, value] of Object.entries(launch.env)) {
      args.push("--env", `${key}=${value}`);
    }
    args.push(launch.name, "--", launch.command, ...launch.args);

    const added = await this.runAgentProcess("codex", args, 15000);
    if (added.code !== 0) {
      throw new Error(`Failed to configure Codex MCP server: ${added.stderr || added.stdout}`);
    }

    return {
      ok: true,
      agent: "Codex",
      server: launch.name,
      command: `codex ${args.map((part) => JSON.stringify(part)).join(" ")}`,
      note: "Restart new Codex sessions so the MCP tool list is loaded at session startup.",
    };
  }

  private async getCodexMcpStatus(): Promise<{ codexCliAvailable: boolean; configured: boolean; error?: string }> {
    try {
      const result = await this.runAgentProcess("codex", ["mcp", "get", "micropython"], 5000);
      if (result.code === 0) {
        return { codexCliAvailable: true, configured: true };
      }

      const listResult = await this.runAgentProcess("codex", ["mcp", "list"], 5000);
      return {
        codexCliAvailable: listResult.code === 0,
        configured: false,
        error: listResult.code === 0 ? undefined : (listResult.stderr || listResult.stdout || "Codex MCP status check failed."),
      };
    } catch (error) {
      return {
        codexCliAvailable: false,
        configured: false,
        error: this.errorText(error),
      };
    }
  }

  private runAgentProcess(command: string, args: string[], timeoutMs: number): Promise<AgentProcessResult> {
    return new Promise((resolve, reject) => {
      const child = spawn(command, args, { shell: false });
      let stdout = "";
      let stderr = "";
      const timeout = setTimeout(() => {
        child.kill();
        reject(new Error(`${command} timed out after ${timeoutMs}ms.`));
      }, timeoutMs);

      child.stdout.on("data", (chunk: Buffer) => {
        stdout += chunk.toString("utf8");
      });
      child.stderr.on("data", (chunk: Buffer) => {
        stderr += chunk.toString("utf8");
      });
      child.on("error", (error) => {
        clearTimeout(timeout);
        reject(error);
      });
      child.on("close", (code) => {
        clearTimeout(timeout);
        resolve({ code, stdout, stderr });
      });
    });
  }

  private errorText(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  private async requireStringInput(
    primary: unknown,
    objectSource: unknown,
    keys: string[],
    options: StringInputOptions,
  ): Promise<StringInputResult> {
    const directValue = this.optionalStringInput(primary, objectSource, keys);
    if (directValue !== undefined) {
      return {
        value: this.validateStringInput(directValue, options),
        interactive: false,
      };
    }

    const value = await vscode.window.showInputBox({
      title: options.title,
      prompt: options.prompt,
      placeHolder: options.placeHolder,
      validateInput: (candidate) => {
        if (options.allowEmpty) {
          return undefined;
        }
        const normalized = options.trim === false ? candidate : candidate.trim();
        return normalized.length > 0 ? undefined : `${options.prompt} is required.`;
      },
    });

    if (value === undefined) {
      throw new Error(`${options.title} cancelled.`);
    }

    return {
      value: this.validateStringInput(value, options),
      interactive: true,
    };
  }

  private optionalStringInput(primary: unknown, objectSource: unknown, keys: string[]): string | undefined {
    if (typeof primary === "string") {
      return primary;
    }

    const object = this.asRecord(primary) ?? this.asRecord(objectSource);
    if (!object) {
      return undefined;
    }

    for (const key of keys) {
      const value = object[key];
      if (typeof value === "string") {
        return value;
      }
    }

    return undefined;
  }

  private validateStringInput(value: string, options: StringInputOptions): string {
    const normalized = options.trim === false ? value : value.trim();
    if (!options.allowEmpty && normalized.length === 0) {
      throw new Error(`${options.prompt} is required.`);
    }
    return normalized;
  }

  private asRecord(value: unknown): Record<string, unknown> | undefined {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return undefined;
    }
    return value as Record<string, unknown>;
  }
}
