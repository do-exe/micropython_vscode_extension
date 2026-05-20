// AI-invokable commands for MicroPython device interaction.
// These commands accept programmatic arguments, while also prompting when
// launched manually from the VS Code command palette.
// This file is the coordinator: it registers commands/tools and delegates
// real work to the smaller AI modules.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import type { BackendServiceClient } from "../backend/host/extension/backendServiceClient";
import type { MicroPythonExtensionController } from "../controller/extensionController";
import { MicroPythonAgentTools } from "./agentTools";
import { AgentMcpConfigurator } from "./mcpConfig";
import { requireStringInput, optionalStringInput } from "./input";
import { completeCommand, createToolResult, ensureOk, serializeResult } from "./result";
import type {
  MicroPythonDeviceStatusInput,
  MicroPythonFilesystemInput,
  MicroPythonRunAndTestInput,
  MicroPythonSoftResetInput,
  MicroPythonSyncProjectInput,
} from "./types";

export class AICommands {
  private readonly output = vscode.window.createOutputChannel("MicroPython AI");
  private readonly agentStatusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 91);
  private readonly agentTools: MicroPythonAgentTools;
  private readonly mcpConfig: AgentMcpConfigurator;
  private languageModelToolsRegistered = false;
  private mcpProviderRegistered = false;

  constructor(
    private readonly backendClient: BackendServiceClient,
    private readonly controller: MicroPythonExtensionController,
  ) {
    this.agentTools = new MicroPythonAgentTools(backendClient, controller, this.output);
    this.mcpConfig = new AgentMcpConfigurator(backendClient);
  }

  public registerCommands(context: vscode.ExtensionContext): void {
    this.mcpConfig.setExtensionPath(context.extensionPath);
    context.subscriptions.push(this.output, this.agentStatusItem);
    this.agentStatusItem.command = "micropython.ai.showAgentMcpStatus";
    this.agentStatusItem.text = "$(tools) MicroPython AI";
    this.agentStatusItem.tooltip = "Show MicroPython AI agent and MCP access status";
    this.agentStatusItem.show();

    this.registerLanguageModelTools(context);
    this.registerMcpServerProvider(context);
    this.registerCommandPaletteCommands(context);
  }

  public async ensureCodexMcpConfigOnActivation(): Promise<void> {
    await this.mcpConfig.ensureCodexConfigOnActivation(this.output);
  }

  public async getDeviceStatus(input: MicroPythonDeviceStatusInput = {}): Promise<unknown> {
    return this.agentTools.getDeviceStatus(input);
  }

  public async syncProjectForAgent(input: MicroPythonSyncProjectInput = {}): Promise<unknown> {
    return this.agentTools.syncProject(input);
  }

  public async runAndTestForAgent(
    input: MicroPythonRunAndTestInput = {},
    token?: vscode.CancellationToken,
  ): Promise<unknown> {
    return this.agentTools.runAndTest(input, token);
  }

  public async filesystemForAgent(input: MicroPythonFilesystemInput): Promise<unknown> {
    return this.agentTools.filesystem(input);
  }

  public async softResetForAgent(input: MicroPythonSoftResetInput = {}): Promise<unknown> {
    return this.agentTools.softReset(input);
  }

  private getSelectedPort(): string {
    const port = this.controller.getSelectedPort();
    if (!port) {
      throw new Error("No MicroPython device selected. Please select a device first.");
    }
    return port;
  }

  private registerCommandPaletteCommands(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
      vscode.commands.registerCommand("micropython.ai.showAgentMcpStatus", async () => {
        await this.showAgentMcpStatusCommand();
      }),
      vscode.commands.registerCommand("micropython.ai.configureAgentMcp", async () => {
        await this.configureAgentMcpCommand();
      }),
      vscode.commands.registerCommand("micropython.ai.runCode", async (input?: unknown) => {
        const code = await requireStringInput(input, input, ["code"], {
          title: "MicroPython AI Run Code",
          prompt: "MicroPython code to run on the selected device",
          placeHolder: "print('hello from MicroPython')",
          trim: false,
        });
        const port = this.getSelectedPort();
        const tempFile = path.join(os.tmpdir(), `micropython_ai_${Date.now()}.py`);

        await fs.promises.writeFile(tempFile, code.value, "utf8");
        try {
          const result = ensureOk(await this.backendClient.runFileInteractive(port, tempFile));
          return completeCommand(this.output, "Run Code", result, code.interactive);
        } finally {
          await fs.promises.unlink(tempFile).catch(() => undefined);
        }
      }),
      vscode.commands.registerCommand("micropython.ai.uploadFile", async (inputOrLocalPath?: unknown, remotePathInput?: unknown) => {
        const localPath = await requireStringInput(inputOrLocalPath, inputOrLocalPath, ["localPath"], {
          title: "MicroPython AI Upload File",
          prompt: "Local file path to upload",
          placeHolder: "/home/user/project/main.py",
        });
        const remotePath = await requireStringInput(remotePathInput, inputOrLocalPath, ["remotePath"], {
          title: "MicroPython AI Upload File",
          prompt: "Device destination path",
          placeHolder: "/main.py",
        });
        const port = this.getSelectedPort();
        const content = await vscode.workspace.fs.readFile(vscode.Uri.file(localPath.value));
        const contentBase64 = Buffer.from(content).toString("base64");
        const result = ensureOk(await this.backendClient.writeWorkspaceFile(port, remotePath.value, contentBase64, {
          create: true,
          overwrite: true,
        }));
        return completeCommand(this.output, "Upload File", result, localPath.interactive || remotePath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.downloadFile", async (inputOrRemotePath?: unknown, localPathInput?: unknown) => {
        const remotePath = await requireStringInput(inputOrRemotePath, inputOrRemotePath, ["remotePath"], {
          title: "MicroPython AI Download File",
          prompt: "Device source path",
          placeHolder: "/boot.py",
        });
        const localPath = await requireStringInput(localPathInput, inputOrRemotePath, ["localPath"], {
          title: "MicroPython AI Download File",
          prompt: "Local destination path",
          placeHolder: "/tmp/boot.py",
        });
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.readWorkspaceFile(port, remotePath.value));
        if (!result.contentBase64) {
          throw new Error(result.error ?? "File content not available.");
        }
        const content = Buffer.from(result.contentBase64, "base64");
        await vscode.workspace.fs.writeFile(vscode.Uri.file(localPath.value), content);
        return completeCommand(this.output, "Download File", {
          ok: true,
          remotePath: remotePath.value,
          localPath: localPath.value,
          bytes: content.length,
        }, remotePath.interactive || localPath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.listFiles", async (input?: unknown) => {
        const port = this.getSelectedPort();
        const remotePath = optionalStringInput(input, input, ["remotePath"]) ?? "/";
        const result = ensureOk(await this.backendClient.listWorkspaceDirectory(port, remotePath));
        return completeCommand(this.output, "List Files", result.entries ?? [], input === undefined);
      }),
      vscode.commands.registerCommand("micropython.ai.createDir", async (input?: unknown) => {
        const remotePath = await requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Create Directory",
          prompt: "Device directory path to create",
          placeHolder: "/lib",
        });
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.createWorkspaceDirectory(port, remotePath.value));
        return completeCommand(this.output, "Create Directory", result, remotePath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.delete", async (input?: unknown) => {
        const remotePath = await requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Delete",
          prompt: "Device file or directory path to delete",
          placeHolder: "/old.py",
        });
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.deleteWorkspaceEntry(port, remotePath.value, true));
        return completeCommand(this.output, "Delete", result, remotePath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.readFile", async (input?: unknown) => {
        const remotePath = await requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI Read File",
          prompt: "Device file path to read",
          placeHolder: "/boot.py",
        });
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.readWorkspaceFile(port, remotePath.value));
        if (!result.contentBase64) {
          throw new Error(result.error ?? "File content not available.");
        }
        const content = Buffer.from(result.contentBase64, "base64").toString("utf8");
        return completeCommand(this.output, "Read File", content, remotePath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.writeFile", async (inputOrRemotePath?: unknown, contentInput?: unknown) => {
        const remotePath = await requireStringInput(inputOrRemotePath, inputOrRemotePath, ["remotePath"], {
          title: "MicroPython AI Write File",
          prompt: "Device file path to write",
          placeHolder: "/main.py",
        });
        const content = await requireStringInput(contentInput, inputOrRemotePath, ["content"], {
          title: "MicroPython AI Write File",
          prompt: "File content",
          placeHolder: "print('hello from MicroPython')",
          trim: false,
          allowEmpty: true,
        });
        const port = this.getSelectedPort();
        const contentBase64 = Buffer.from(content.value, "utf8").toString("base64");
        const result = ensureOk(await this.backendClient.writeWorkspaceFile(port, remotePath.value, contentBase64, {
          create: true,
          overwrite: true,
        }));
        return completeCommand(this.output, "Write File", result, remotePath.interactive || content.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.stat", async (input?: unknown) => {
        const remotePath = await requireStringInput(input, input, ["remotePath"], {
          title: "MicroPython AI File Stats",
          prompt: "Device file or directory path",
          placeHolder: "/boot.py",
        });
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.statWorkspaceEntry(port, remotePath.value));
        return completeCommand(this.output, "File Stats", result, remotePath.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.sendRepl", async (input?: unknown) => {
        const command = await requireStringInput(input, input, ["command"], {
          title: "MicroPython AI Send REPL Command",
          prompt: "REPL command to send",
          placeHolder: "x = 42",
        });
        this.getSelectedPort();
        await this.backendClient.sendTerminalInput(command.value + "\n");
        return completeCommand(this.output, "Send REPL Command", { ok: true }, command.interactive);
      }),
      vscode.commands.registerCommand("micropython.ai.softReset", async () => {
        const port = this.getSelectedPort();
        const result = ensureOk(await this.backendClient.softReset(port, 30));
        return completeCommand(this.output, "Soft Reset", result, true);
      }),
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
        invoke: async (options) => createToolResult(await this.getDeviceStatus(options.input ?? {})),
        prepareInvocation: () => ({
          invocationMessage: "Checking MicroPython device status",
        }),
      }),
      lm.registerTool<MicroPythonSyncProjectInput>("micropython_sync_project", {
        invoke: async (options) => createToolResult(await this.syncProjectForAgent(options.input ?? {})),
        prepareInvocation: (options) => ({
          invocationMessage: `Syncing MicroPython project${options.input?.projectFolder ? `: ${options.input.projectFolder}` : ""}`,
        }),
      }),
      lm.registerTool<MicroPythonRunAndTestInput>("micropython_run_and_test", {
        invoke: async (options, token) => createToolResult(await this.runAndTestForAgent(options.input ?? {}, token)),
        prepareInvocation: (options) => ({
          invocationMessage: `Running MicroPython code${options.input?.localFile ? `: ${path.basename(options.input.localFile)}` : ""}`,
        }),
      }),
      lm.registerTool<MicroPythonFilesystemInput>("micropython_filesystem", {
        invoke: async (options) => createToolResult(await this.filesystemForAgent(options.input ?? { operation: "list" })),
        prepareInvocation: (options) => ({
          invocationMessage: `Running MicroPython filesystem ${options.input?.operation ?? "operation"}`,
        }),
      }),
      lm.registerTool<MicroPythonSoftResetInput>("micropython_soft_reset", {
        invoke: async (options) => createToolResult(await this.softResetForAgent(options.input ?? {})),
        prepareInvocation: () => ({
          invocationMessage: "Soft resetting MicroPython device",
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
      provideMcpServerDefinitions: async () => this.mcpConfig.provideVscodeMcpServerDefinitions(context),
    }));
    this.mcpProviderRegistered = true;
    this.refreshAgentStatusIndicator();
  }

  private async showAgentMcpStatusCommand(): Promise<void> {
    const status = await this.mcpConfig.getStatus(this.languageModelToolsRegistered, this.mcpProviderRegistered);
    this.output.appendLine(`[${new Date().toISOString()}] Agent MCP Status`);
    this.output.appendLine(serializeResult(status));
    this.output.appendLine("");
    this.output.show(false);

    const summary = [
      `VS Code tools: ${this.languageModelToolsRegistered ? "registered" : "not available"}`,
      `VS Code MCP: ${this.mcpProviderRegistered ? "published" : "not available"}`,
      `Workspace mcp.json: ${status.vscodeWorkspaceMcp.configured ? "configured" : "not configured"}`,
      `Codex MCP: ${status.codexGlobalMcp.configured ? "configured" : "not configured"}`,
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
        description: "Writes Codex config.toml so Codex sessions receive MicroPython tools.",
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

    const launch = await this.mcpConfig.getLaunchConfig();
    const results: unknown[] = [];

    if (choice.target === "vscode" || choice.target === "both") {
      results.push(await this.mcpConfig.writeWorkspaceConfig(launch));
    }

    if (choice.target === "codex" || choice.target === "both") {
      const confirmation = await vscode.window.showWarningMessage(
        "Configure Codex global MCP access for MicroPython? This updates Codex config.toml so new Codex sessions can see the MicroPython tools.",
        { modal: true },
        "Configure Codex",
      );
      if (confirmation === "Configure Codex") {
        results.push(await this.mcpConfig.configureCodex(launch));
      }
    }

    this.refreshAgentStatusIndicator();
    this.output.appendLine(`[${new Date().toISOString()}] Configure Agent MCP Access`);
    this.output.appendLine(serializeResult(results));
    this.output.appendLine("");
    this.output.show(false);
    void vscode.window.showInformationMessage("MicroPython agent MCP access configuration complete. Restart or refresh any already-open agent sessions.");
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
}
