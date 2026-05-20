// Agent-facing MicroPython tool operations.
// This file contains the actual device status, sync, run/test, filesystem,
// and soft reset implementations used by VS Code language-model tools.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

import type { BackendServiceClient } from "../backend/host/extension/backendServiceClient";
import type { MicroPythonExtensionController } from "../controller/extensionController";
import {
  normalizeRemotePath,
  normalizeRemoteRoot,
  normalizeTimeout,
  resolveAgentPort,
  resolveProjectFolder,
  resolveRunFile,
} from "./paths";
import { completeCommand, ensureOk } from "./result";
import type {
  MicroPythonDeviceStatusInput,
  MicroPythonFilesystemInput,
  MicroPythonRunAndTestInput,
  MicroPythonSoftResetInput,
  MicroPythonSyncProjectInput,
} from "./types";

export class MicroPythonAgentTools {
  constructor(
    private readonly backendClient: BackendServiceClient,
    private readonly controller: MicroPythonExtensionController,
    private readonly output: vscode.OutputChannel,
  ) {}

  public async getDeviceStatus(input: MicroPythonDeviceStatusInput = {}): Promise<unknown> {
    const scan = ensureOk(await this.backendClient.scan());
    const selectedPort = input.port?.trim() || this.controller.getSelectedPort();
    const devices = scan.devices ?? [];
    const selectedDevice = selectedPort
      ? devices.find((device) => device.port === selectedPort)
      : undefined;
    return completeCommand(this.output, "Device Status", {
      ok: true,
      selectedPort: selectedPort ?? null,
      selectedDevice: selectedDevice ?? null,
      devices,
      guidance: selectedPort || devices.length === 1
        ? "Use micropython_run_and_test for upload/run/test workflows. Do not use mpremote, ampy, esptool, or raw serial directly unless this tool reports unsupported."
        : "Ask the user to select a MicroPython device, or pass a port explicitly if the intended device is known.",
    }, false);
  }

  public async syncProject(input: MicroPythonSyncProjectInput = {}): Promise<unknown> {
    const port = await resolveAgentPort(this.backendClient, this.controller, input.port);
    const projectFolder = resolveProjectFolder(input.projectFolder, undefined);
    if (!projectFolder) {
      throw new Error("No project folder was provided, and no file workspace is open.");
    }
    const remoteRoot = normalizeRemoteRoot(input.remoteRoot);
    const deleteExtraneous = input.deleteExtraneous === true;
    const progressLines: string[] = [];

    const result = ensureOk(await this.backendClient.syncFolder(
      port,
      projectFolder,
      remoteRoot,
      deleteExtraneous,
      (line: string, isError: boolean) => {
        progressLines.push(isError ? `[ERROR] ${line}` : line);
      },
    ));

    return completeCommand(this.output, "Sync Project", {
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

  public async runAndTest(
    input: MicroPythonRunAndTestInput = {},
    token?: vscode.CancellationToken,
  ): Promise<unknown> {
    const port = await resolveAgentPort(this.backendClient, this.controller, input.port);
    const workspaceFolder = resolveProjectFolder(input.projectFolder, input.localFile);
    const remoteRoot = normalizeRemoteRoot(input.remoteRoot);
    const timeoutSeconds = normalizeTimeout(input.timeoutSeconds);
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
        return completeCommand(this.output, "Run And Test", {
          ok: false,
          port,
          failedStep: "syncProject",
          error: syncResult.error ?? "MicroPython project sync failed.",
          steps,
        }, false);
      }
    }

    let tempFile: string | undefined;
    const localFile = await resolveRunFile(input, workspaceFolder);
    let runFile = localFile;
    if (input.code !== undefined) {
      tempFile = path.join(os.tmpdir(), `micropython_agent_${Date.now()}.py`);
      await fs.promises.writeFile(tempFile, input.code, "utf8");
      runFile = tempFile;
    }

    if (!runFile) {
      return completeCommand(this.output, "Run And Test", {
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

      return completeCommand(this.output, "Run And Test", {
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

  public async filesystem(input: MicroPythonFilesystemInput): Promise<unknown> {
    const port = await resolveAgentPort(this.backendClient, this.controller, input.port);
    const operation = input.operation;
    const remotePath = normalizeRemotePath(input.path, operation === "list" ? "/" : undefined);

    let result: unknown;
    switch (operation) {
      case "list":
        result = ensureOk(await this.backendClient.listWorkspaceDirectory(port, remotePath));
        break;
      case "read": {
        const readResult = ensureOk(await this.backendClient.readWorkspaceFile(port, remotePath));
        result = {
          ...readResult,
          content: readResult.contentBase64 ? Buffer.from(readResult.contentBase64, "base64").toString("utf8") : "",
        };
        break;
      }
      case "write": {
        const contentBase64 = input.contentBase64 ?? Buffer.from(input.content ?? "", "utf8").toString("base64");
        result = ensureOk(await this.backendClient.writeWorkspaceFile(port, remotePath, contentBase64, {
          create: true,
          overwrite: input.overwrite !== false,
        }));
        break;
      }
      case "mkdir":
        result = ensureOk(await this.backendClient.createWorkspaceDirectory(port, remotePath));
        break;
      case "rename": {
        const newPath = normalizeRemotePath(input.newPath);
        result = ensureOk(await this.backendClient.renameWorkspaceEntry(port, remotePath, newPath, input.overwrite === true));
        break;
      }
      case "delete":
        result = ensureOk(await this.backendClient.deleteWorkspaceEntry(port, remotePath, input.recursive !== false));
        break;
      case "stat":
        result = ensureOk(await this.backendClient.statWorkspaceEntry(port, remotePath));
        break;
      default:
        throw new Error(`Unsupported MicroPython filesystem operation: ${String(operation)}`);
    }

    return completeCommand(this.output, "Filesystem", {
      ok: true,
      port,
      operation,
      path: remotePath,
      result,
      guidance: "Filesystem operation used the extension backend. Do not retry with mpremote, ampy, esptool, or raw serial unless this result says unsupported.",
    }, false);
  }

  public async softReset(input: MicroPythonSoftResetInput = {}): Promise<unknown> {
    const port = await resolveAgentPort(this.backendClient, this.controller, input.port);
    const timeoutSeconds = normalizeTimeout(input.timeoutSeconds ?? 30);
    const result = ensureOk(await this.backendClient.softReset(port, timeoutSeconds));
    return completeCommand(this.output, "Soft Reset", {
      ok: true,
      port,
      timeoutSeconds,
      result,
      guidance: "Soft reset used the extension backend. Inspect output/promptSeen if the device still appears stuck.",
    }, false);
  }
}
