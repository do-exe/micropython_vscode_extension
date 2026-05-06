import * as vscode from "vscode";

import { MicroPythonExtensionController } from "./controller/extensionController";

let controller: MicroPythonExtensionController | undefined;

export function activate(context: vscode.ExtensionContext): void {
  controller = new MicroPythonExtensionController(context);
  void controller.start();
}

export function deactivate(): void {
  if (controller) {
    controller.dispose();
    controller = undefined;
  }
}
