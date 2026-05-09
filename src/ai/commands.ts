// AI-invokable commands for MicroPython device interaction.
// These commands accept programmatic arguments, while also prompting when
// launched manually from the VS Code command palette.

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";
import type { BackendServiceClient } from "../backend/backendServiceClient";
import type { MicroPythonExtensionController } from "../controller/extensionController";

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

export class AICommands {
  private readonly output = vscode.window.createOutputChannel("MicroPython AI");

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
    context.subscriptions.push(this.output);

    context.subscriptions.push(
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
