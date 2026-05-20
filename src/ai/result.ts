// Shared result helpers for AI commands and language-model tools.
// This file validates backend results, serializes output, writes command logs,
// and adapts results to VS Code LanguageModelToolResult objects.

import * as vscode from "vscode";
import type { BackendOkResult } from "./types";

export function ensureOk<T extends BackendOkResult>(result: T): T {
  if (result.ok === false) {
    throw new Error(result.error ?? "MicroPython operation failed.");
  }
  return result;
}

export function serializeResult(result: unknown): string {
  if (typeof result === "string") {
    return result;
  }
  if (result === undefined) {
    return "undefined";
  }
  return JSON.stringify(result, null, 2) ?? String(result);
}

export function completeCommand<T>(
  output: vscode.OutputChannel,
  label: string,
  result: T,
  interactive: boolean,
): T {
  output.appendLine(`[${new Date().toISOString()}] ${label}`);
  output.appendLine(serializeResult(result));
  output.appendLine("");

  if (interactive) {
    output.show(true);
    void vscode.window.showInformationMessage(`MicroPython AI ${label} complete. See the MicroPython AI output.`);
  }

  return result;
}

export function createToolResult(result: unknown): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(serializeResult(result)),
  ]);
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
