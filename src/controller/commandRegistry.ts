import * as vscode from "vscode";

import type { WorkspaceCommandTarget } from "./types";

export type MicroPythonCommandHandlers = {
  selectDevice: () => Promise<void>;
  openTerminal: () => Promise<void>;
  softResetDevice: () => Promise<void>;
  runCurrentFile: () => Promise<void>;
  runInteractiveFile: () => Promise<void>;
  runAutoFile: () => Promise<void>;
  linkFolder: () => Promise<void>;
  syncFolder: (uri?: vscode.Uri) => Promise<void>;
  fetchWorkspace: () => Promise<void>;
  fetchWorkspacePartial: () => Promise<void>;
  confirmWorkspacePartialFetch: () => Promise<void>;
  clearWorkspacePartialFetchSelection: () => Promise<void>;
  cancelWorkspacePartialFetch: () => Promise<void>;
  deleteWorkspaceSelection: () => Promise<void>;
  confirmWorkspaceDeleteSelection: () => Promise<void>;
  clearWorkspaceDeleteSelection: () => Promise<void>;
  cancelWorkspaceDeleteSelection: () => Promise<void>;
  clearAllFiles: () => Promise<void>;
  refreshWorkspace: () => Promise<void>;
  createWorkspaceFile: (target?: WorkspaceCommandTarget) => Promise<void>;
  createWorkspaceFolder: (target?: WorkspaceCommandTarget) => Promise<void>;
  copyWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  pasteWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  renameWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  deleteWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  showWorkspaceEntryProperties: (target?: WorkspaceCommandTarget) => Promise<void>;
  uploadWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  downloadWorkspaceEntry: (target?: WorkspaceCommandTarget) => Promise<void>;
  mountWorkspace: () => Promise<void>;
  openWorkspaceFile: (remotePath: string, port?: string) => Promise<void>;
};

export function registerMicroPythonCommands(
  context: vscode.ExtensionContext,
  handlers: MicroPythonCommandHandlers,
): void {
  const register = <Args extends unknown[]>(
    command: string,
    handler: (...args: Args) => Promise<void>,
  ): vscode.Disposable => vscode.commands.registerCommand(command, (...args: Args) => handler(...args));

  context.subscriptions.push(
    register("micropython.selectDevice", handlers.selectDevice),
    register("micropython.openTerminal", handlers.openTerminal),
    register("micropython.softResetDevice", handlers.softResetDevice),
    register("micropython.runCurrentFile", handlers.runCurrentFile),
    register("micropython.runInteractiveFile", handlers.runInteractiveFile),
    register("micropython.runFileEditor", handlers.runAutoFile),
    register("micropython.linkFolder", handlers.linkFolder),
    register("micropython.syncFolder", handlers.syncFolder),
    register("micropython.fetchWorkspace", handlers.fetchWorkspace),
    register("micropython.fetchWorkspacePartial", handlers.fetchWorkspacePartial),
    register("micropython.fetchWorkspacePartialConfirm", handlers.confirmWorkspacePartialFetch),
    register("micropython.fetchWorkspacePartialClear", handlers.clearWorkspacePartialFetchSelection),
    register("micropython.fetchWorkspacePartialCancel", handlers.cancelWorkspacePartialFetch),
    register("micropython.deleteWorkspaceSelection", handlers.deleteWorkspaceSelection),
    register("micropython.deleteWorkspaceSelectionConfirm", handlers.confirmWorkspaceDeleteSelection),
    register("micropython.deleteWorkspaceSelectionClear", handlers.clearWorkspaceDeleteSelection),
    register("micropython.deleteWorkspaceSelectionCancel", handlers.cancelWorkspaceDeleteSelection),
    register("micropython.clearAllFiles", handlers.clearAllFiles),
    register("micropython.refreshWorkspace", handlers.refreshWorkspace),
    register("micropython.newWorkspaceFile", handlers.createWorkspaceFile),
    register("micropython.newWorkspaceFolder", handlers.createWorkspaceFolder),
    register("micropython.copyWorkspaceEntry", handlers.copyWorkspaceEntry),
    register("micropython.pasteWorkspaceEntry", handlers.pasteWorkspaceEntry),
    register("micropython.renameWorkspaceEntry", handlers.renameWorkspaceEntry),
    register("micropython.deleteWorkspaceEntry", handlers.deleteWorkspaceEntry),
    register("micropython.showWorkspaceEntryProperties", handlers.showWorkspaceEntryProperties),
    register("micropython.uploadWorkspaceEntry", handlers.uploadWorkspaceEntry),
    register("micropython.downloadWorkspaceEntry", handlers.downloadWorkspaceEntry),
    register("micropython.mountWorkspace", handlers.mountWorkspace),
    register("micropython.openWorkspaceFile", handlers.openWorkspaceFile),
  );
}
