// Path, port, and run-target resolution for AI workflows.
// This file normalizes MicroPython device paths and finds the selected port,
// project folder, and Python file to run.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import type { BackendServiceClient } from "../backend/host/extension/backendServiceClient";
import type { MicroPythonExtensionController } from "../controller/extensionController";
import type { MicroPythonRunAndTestInput } from "./types";
import { ensureOk } from "./result";

export async function resolveAgentPort(
  backendClient: BackendServiceClient,
  controller: MicroPythonExtensionController,
  explicitPort?: string,
): Promise<string> {
  const requestedPort = explicitPort?.trim();
  if (requestedPort) {
    return requestedPort;
  }

  const selectedPort = controller.getSelectedPort();
  if (selectedPort) {
    return selectedPort;
  }

  const scan = ensureOk(await backendClient.scan());
  const devices = scan.devices ?? [];
  if (devices.length === 1) {
    return devices[0].port;
  }
  if (devices.length > 1) {
    throw new Error(`Multiple MicroPython devices detected (${devices.map((device) => device.port).join(", ")}). Pass the intended port or ask the user to select a device.`);
  }
  throw new Error("No MicroPython device detected. Connect a device, then try again.");
}

export function resolveProjectFolder(inputFolder: string | undefined, localFile: string | undefined): string | undefined {
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

export async function resolveRunFile(
  input: MicroPythonRunAndTestInput,
  projectFolder: string | undefined,
): Promise<string | undefined> {
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

export function normalizeRemoteRoot(remoteRoot: string | undefined): string {
  const text = remoteRoot?.trim() || "/";
  const normalized = path.posix.normalize(text.replace(/\\/g, "/"));
  if (normalized === "." || normalized === "") {
    return "/";
  }
  return normalized.startsWith("/") ? normalized : `/${normalized}`;
}

export function normalizeRemotePath(remotePath: string | undefined, defaultPath?: string): string {
  const text = remotePath?.trim() || defaultPath;
  if (!text) {
    throw new Error("A device path is required for this MicroPython filesystem operation.");
  }
  const normalized = path.posix.normalize(text.replace(/\\/g, "/"));
  if (normalized === "." || normalized === "") {
    return "/";
  }
  return normalized.startsWith("/") ? normalized : `/${normalized}`;
}

export function normalizeTimeout(timeoutSeconds: number | undefined): number {
  if (typeof timeoutSeconds !== "number" || !Number.isFinite(timeoutSeconds)) {
    return vscode.workspace.getConfiguration("micropython").get<number>("runTimeoutSeconds", 0);
  }
  return Math.max(0, Math.min(600, timeoutSeconds));
}
