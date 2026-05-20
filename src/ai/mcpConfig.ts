// MCP configuration and status support for AI agents.
// This file writes VS Code workspace MCP config, Codex MCP config, and builds
// the MicroPython MCP launch definition.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import type { BackendServiceClient } from "../backend/host/extension/backendServiceClient";
import {
  BACKEND_ROOT_RELATIVE_DIR,
  MCP_ADAPTER_MODULE,
  PYTHON_SERVICE_PARENT_RELATIVE_DIR,
  resolveExtensionBackendPath,
  withBackendPythonPath,
} from "../backend/host/extension/paths";
import { asRecord } from "./input";
import { errorText, serializeResult } from "./result";
import type { AgentMcpLaunchConfig, AgentMcpStatus, CodexMcpConfigTarget } from "./types";

const CODEX_MCP_SERVER_NAME = "micropython";
const CODEX_MCP_MANAGED_COMMENT = "# Managed by MicroPython VS Code Extension.";
const CODEX_MCP_REFRESH_COMMENT = "# This MCP server block is refreshed on extension activation.";

export class AgentMcpConfigurator {
  private extensionPath: string | undefined;

  constructor(private readonly backendClient: BackendServiceClient) {}

  public setExtensionPath(extensionPath: string): void {
    this.extensionPath = extensionPath;
  }

  public async ensureCodexConfigOnActivation(output: vscode.OutputChannel): Promise<void> {
    try {
      const launch = await this.getLaunchConfig();
      const result = await this.writeCodexConfig(launch);
      output.appendLine(`[${new Date().toISOString()}] Codex MCP Auto Configuration`);
      output.appendLine(serializeResult(result));
      output.appendLine("");
    } catch (error) {
      output.appendLine(`[${new Date().toISOString()}] Codex MCP Auto Configuration Failed`);
      output.appendLine(errorText(error));
      output.appendLine("");
    }
  }

  public async provideVscodeMcpServerDefinitions(context: vscode.ExtensionContext): Promise<vscode.McpStdioServerDefinition[]> {
    try {
      const launch = await this.backendClient.getBundledPythonLaunch();
      const backendPythonPaths = this.backendPythonPaths(context.extensionPath);
      const server = new vscode.McpStdioServerDefinition(
        "MicroPython",
        launch.pythonPath,
        ["-m", MCP_ADAPTER_MODULE],
        this.toMcpEnvironment(withBackendPythonPath(launch.env, backendPythonPaths)),
        String(context.extension.packageJSON?.version ?? "0.0.0"),
      );
      server.cwd = vscode.Uri.file(backendPythonPaths[0]);
      return [server];
    } catch {
      return [];
    }
  }

  public async getStatus(
    languageModelToolsRegistered: boolean,
    mcpProviderRegistered: boolean,
  ): Promise<AgentMcpStatus> {
    const workspaceMcpPath = this.getWorkspaceMcpConfigPath();
    let workspaceConfigured = false;
    let workspaceError: string | undefined;
    try {
      workspaceConfigured = workspaceMcpPath ? await this.workspaceMcpConfigHasMicroPython(workspaceMcpPath) : false;
    } catch (error) {
      workspaceError = errorText(error);
    }

    const codexStatus = await this.getCodexStatus();
    return {
      ok: true,
      server: CODEX_MCP_SERVER_NAME,
      vscodeLanguageModelTools: {
        available: Boolean(vscode.lm && typeof vscode.lm.registerTool === "function"),
        registered: languageModelToolsRegistered,
      },
      vscodeExtensionMcpProvider: {
        available: Boolean(vscode.lm && typeof vscode.lm.registerMcpServerDefinitionProvider === "function"),
        registered: mcpProviderRegistered,
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
        "Use MicroPython: Configure AI Agent MCP Access to write .vscode/mcp.json and/or refresh Codex config.toml.",
        "Restart or refresh already-open agent sessions after changing MCP configuration.",
      ],
    };
  }

  public async getLaunchConfig(): Promise<AgentMcpLaunchConfig> {
    const launch = await this.backendClient.getBundledPythonLaunch();
    const backendPythonPaths = this.backendPythonPaths(this.extensionPath ?? this.controllerExtensionPath());
    return {
      name: CODEX_MCP_SERVER_NAME,
      command: launch.pythonPath,
      args: ["-m", MCP_ADAPTER_MODULE],
      env: this.minimalMcpEnvironment(withBackendPythonPath(launch.env, backendPythonPaths)),
      backendPythonPaths,
    };
  }

  public async writeWorkspaceConfig(launch: AgentMcpLaunchConfig): Promise<unknown> {
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

    const servers = asRecord(config.servers) ?? {};
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

  public async configureCodex(launch: AgentMcpLaunchConfig): Promise<unknown> {
    const result = await this.writeCodexConfig(launch);
    return {
      ok: true,
      agent: "Codex",
      server: launch.name,
      targets: result.targets,
      note: "Restart new Codex sessions so the MCP tool list is loaded at session startup.",
    };
  }

  public async writeCodexConfig(launch: AgentMcpLaunchConfig): Promise<{ ok: boolean; agent: string; server: string; targets: unknown[] }> {
    const targets = await this.getCodexConfigTargets();
    const results: Array<Record<string, unknown>> = [];

    for (const target of targets) {
      try {
        results.push({
          ok: true,
          ...(await this.writeCodexConfigPath(target.path, launch)),
          required: target.required,
        });
      } catch (error) {
        results.push({
          ok: false,
          path: target.path,
          required: target.required,
          error: errorText(error),
        });
      }
    }

    const requiredFailures = results.filter((result) => result.ok === false && result.required === true);
    if (requiredFailures.length > 0) {
      throw new Error(`Failed to update Codex MCP config: ${serializeResult(requiredFailures)}`);
    }

    return {
      ok: true,
      agent: "Codex",
      server: launch.name,
      targets: results,
    };
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
        normalized[key] = key === "PATH" ? this.sanitizeMcpPath(value) : value;
      }
    }
    return normalized;
  }

  private sanitizeMcpPath(value: string): string {
    const blockedFragments = [
      `${path.sep}.codex${path.sep}tmp${path.sep}`,
      `${path.sep}.vscode${path.sep}extensions${path.sep}openai.chatgpt-`,
    ];
    const entries: string[] = [];
    const seen = new Set<string>();

    for (const entry of value.split(path.delimiter)) {
      if (!entry || blockedFragments.some((fragment) => entry.includes(fragment)) || seen.has(entry)) {
        continue;
      }
      seen.add(entry);
      entries.push(entry);
    }

    return entries.join(path.delimiter);
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

  private async getCodexStatus(): Promise<{ configured: boolean; paths: string[]; checkedPaths: string[]; error?: string }> {
    const targets = await this.getCodexConfigTargets();
    const backendPythonPaths = this.backendPythonPaths(this.extensionPath ?? this.controllerExtensionPath());
    const configuredPaths: string[] = [];
    const errors: string[] = [];

    for (const target of targets) {
      try {
        if (await this.codexConfigHasMicroPython(target.path, backendPythonPaths)) {
          configuredPaths.push(target.path);
        }
      } catch (error) {
        errors.push(`${target.path}: ${errorText(error)}`);
      }
    }

    return {
      configured: configuredPaths.length > 0,
      paths: configuredPaths,
      checkedPaths: targets.map((target) => target.path),
      error: errors.length > 0 ? errors.join("; ") : undefined,
    };
  }

  private async getCodexConfigTargets(): Promise<CodexMcpConfigTarget[]> {
    const targets: CodexMcpConfigTarget[] = [];
    const seen = new Set<string>();
    const addTarget = (configPath: string, required: boolean): void => {
      const normalized = path.normalize(configPath);
      if (!seen.has(normalized)) {
        seen.add(normalized);
        targets.push({ path: normalized, required });
      }
    };

    const codexHome = process.env.CODEX_HOME?.trim();
    if (codexHome) {
      addTarget(path.join(codexHome, "config.toml"), true);
    }

    addTarget(path.join(os.homedir(), ".codex", "config.toml"), true);

    if (process.platform === "linux") {
      const snapCurrentPath = path.join(os.homedir(), "snap", "codex", "current");
      try {
        const snapCurrentStat = await fs.promises.stat(snapCurrentPath);
        if (snapCurrentStat.isDirectory()) {
          const resolvedSnapPath = await fs.promises.realpath(snapCurrentPath);
          addTarget(path.join(resolvedSnapPath, "config.toml"), false);
        }
      } catch {
        // Snap Codex is optional. The normal Codex config above is always maintained.
      }
    }

    return targets;
  }

  private async writeCodexConfigPath(configPath: string, launch: AgentMcpLaunchConfig): Promise<Record<string, unknown>> {
    let existing = "";
    let existed = true;
    try {
      existing = await fs.promises.readFile(configPath, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        throw error;
      }
      existed = false;
    }

    const next = this.updateCodexToml(existing, launch);
    if (next !== existing) {
      await fs.promises.mkdir(path.dirname(configPath), { recursive: true });
      await fs.promises.writeFile(configPath, next, "utf8");
    }

    return {
      path: configPath,
      changed: next !== existing,
      created: !existed,
    };
  }

  private backendPythonPaths(extensionPath: string): string[] {
    return [
      resolveExtensionBackendPath(extensionPath, PYTHON_SERVICE_PARENT_RELATIVE_DIR),
      resolveExtensionBackendPath(extensionPath, BACKEND_ROOT_RELATIVE_DIR),
    ];
  }

  private async codexConfigHasMicroPython(configPath: string, backendPythonPaths: string[]): Promise<boolean> {
    let raw: string;
    try {
      raw = await fs.promises.readFile(configPath, "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return false;
      }
      throw error;
    }

    return this.tomlHasTable(raw, `mcp_servers.${CODEX_MCP_SERVER_NAME}`)
      && backendPythonPaths.every((backendPythonPath) => raw.includes(backendPythonPath));
  }

  private updateCodexToml(source: string, launch: AgentMcpLaunchConfig): string {
    const targetTables = new Set([
      `mcp_servers.${launch.name}`,
      `mcp_servers.${launch.name}.env`,
    ]);
    const lines = source.split(/\r?\n/);
    const kept: string[] = [];
    let skippingManagedTable = false;

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed === CODEX_MCP_MANAGED_COMMENT || trimmed === CODEX_MCP_REFRESH_COMMENT) {
        continue;
      }

      const tableName = this.parseTomlTableName(trimmed);
      if (tableName !== undefined) {
        skippingManagedTable = targetTables.has(tableName);
      }

      if (!skippingManagedTable) {
        kept.push(line);
      }
    }

    const prefix = kept.join("\n").trimEnd();
    const block = this.buildCodexTomlBlock(launch);
    return `${prefix ? `${prefix}\n\n` : ""}${block}\n`;
  }

  private buildCodexTomlBlock(launch: AgentMcpLaunchConfig): string {
    const lines = [
      CODEX_MCP_MANAGED_COMMENT,
      CODEX_MCP_REFRESH_COMMENT,
      `[mcp_servers.${launch.name}]`,
      `command = ${this.tomlString(launch.command)}`,
      `args = ${this.tomlStringArray(launch.args)}`,
      "",
      `[mcp_servers.${launch.name}.env]`,
    ];

    for (const [key, value] of Object.entries(launch.env)) {
      lines.push(`${this.tomlKey(key)} = ${this.tomlString(value)}`);
    }

    return lines.join("\n");
  }

  private tomlHasTable(source: string, tableName: string): boolean {
    return source.split(/\r?\n/).some((line) => this.parseTomlTableName(line.trim()) === tableName);
  }

  private parseTomlTableName(line: string): string | undefined {
    const match = /^\[([^\]]+)\]$/.exec(line);
    return match?.[1].trim();
  }

  private tomlKey(key: string): string {
    return /^[A-Za-z0-9_-]+$/.test(key) ? key : this.tomlString(key);
  }

  private tomlStringArray(values: string[]): string {
    return `[${values.map((value) => this.tomlString(value)).join(", ")}]`;
  }

  private tomlString(value: string): string {
    return `"${value.replace(/\\/g, "\\\\").replace(/"/g, "\\\"").replace(/\u0008/g, "\\b").replace(/\t/g, "\\t").replace(/\n/g, "\\n").replace(/\f/g, "\\f").replace(/\r/g, "\\r")}"`;
  }
}
