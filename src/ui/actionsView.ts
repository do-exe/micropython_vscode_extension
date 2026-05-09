import * as vscode from "vscode";

type MicroPythonActionDefinition = {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly command: string;
  readonly icon: string;
};

class MicroPythonActionItem extends vscode.TreeItem {
  constructor(action: MicroPythonActionDefinition) {
    super(action.label, vscode.TreeItemCollapsibleState.None);
    this.id = action.id;
    if (action.description) {
      this.description = action.description;
      this.tooltip = `${action.label}\n${action.description}`;
    } else {
      this.tooltip = action.label;
    }
    this.command = {
      command: action.command,
      title: action.label,
    };
    this.iconPath = new vscode.ThemeIcon(action.icon);
    this.contextValue = "micropythonAction";
  }
}

const ACTIONS: readonly MicroPythonActionDefinition[] = [
  {
    id: "selectDevice",
    label: "Select Device",
    command: "micropython.selectDevice",
    icon: "plug",
  },
  {
    id: "softResetDevice",
    label: "Soft Reset",
    command: "micropython.softResetDevice",
    icon: "debug-restart",
  },
  {
    id: "runCurrentFile",
    label: "Run Non-Interactive",
    command: "micropython.runCurrentFile",
    icon: "play",
  },
  {
    id: "runInteractiveFile",
    label: "Run Interactive",
    command: "micropython.runInteractiveFile",
    icon: "terminal",
  },
  {
    id: "openTerminal",
    label: "Open Terminal",
    command: "micropython.openTerminal",
    icon: "chip",
  },
  {
    id: "linkFolder",
    label: "Link Folder",
    command: "micropython.linkFolder",
    icon: "link",
  },
  {
    id: "uploadWorkspaceEntry",
    label: "Upload File/Folder",
    description: "Copy local files or folders to the device",
    command: "micropython.uploadWorkspaceEntry",
    icon: "cloud-upload",
  },
];

export class MicroPythonActionsViewProvider implements vscode.TreeDataProvider<MicroPythonActionItem> {
  public getTreeItem(element: MicroPythonActionItem): vscode.TreeItem {
    return element;
  }

  public getChildren(element?: MicroPythonActionItem): MicroPythonActionItem[] {
    if (element) {
      return [];
    }
    return ACTIONS.map((action) => new MicroPythonActionItem(action));
  }
}
