// Shared input helpers for AI and command-palette commands.
// This file extracts string arguments from programmatic inputs and falls back
// to VS Code input boxes for interactive command use.

import * as vscode from "vscode";
import type { StringInputOptions, StringInputResult } from "./types";

export async function requireStringInput(
  primary: unknown,
  objectSource: unknown,
  keys: string[],
  options: StringInputOptions,
): Promise<StringInputResult> {
  const directValue = optionalStringInput(primary, objectSource, keys);
  if (directValue !== undefined) {
    return {
      value: validateStringInput(directValue, options),
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
    value: validateStringInput(value, options),
    interactive: true,
  };
}

export function optionalStringInput(primary: unknown, objectSource: unknown, keys: string[]): string | undefined {
  if (typeof primary === "string") {
    return primary;
  }

  const object = asRecord(primary) ?? asRecord(objectSource);
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

export function validateStringInput(value: string, options: StringInputOptions): string {
  const normalized = options.trim === false ? value : value.trim();
  if (!options.allowEmpty && normalized.length === 0) {
    throw new Error(`${options.prompt} is required.`);
  }
  return normalized;
}

export function asRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}
