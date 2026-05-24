import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

export type PlatformId = "micropython" | "arduino" | "espidf" | "stm";

type PlatformDefinition = {
  readonly id: PlatformId;
  readonly label: string;
};

export type PlatformRecentProject = {
  readonly platformId: PlatformId;
  readonly platformLabel: string;
  readonly name: string;
  readonly folder: string;
};

export type PlatformProjectSetup = {
  readonly platformId: PlatformId;
  readonly projectName: string;
  readonly parentFolder: string;
  readonly boardTarget?: string;
  readonly port?: string;
};

type PlatformActionDefinition = {
  readonly id: string;
  readonly label: string;
  readonly command: string;
  readonly icon: string;
  readonly description?: string;
};

const PLATFORMS: readonly PlatformDefinition[] = [
  {
    id: "micropython",
    label: "MicroPython",
  },
  {
    id: "arduino",
    label: "Arduino",
  },
  {
    id: "espidf",
    label: "ESP-IDF",
  },
  {
    id: "stm",
    label: "STM",
  },
];

const PLATFORM_ACTIONS: Record<PlatformId, readonly PlatformActionDefinition[]> = {
  micropython: [],
  arduino: [
    {
      id: "chooseFramework",
      label: "Choose Framework",
      command: "micropython.showPlatformLauncher",
      icon: "arrow-left",
    },
    {
      id: "arduinoStatus",
      label: "Toolchain Status",
      command: "micropython.arduino.toolchainStatus",
      icon: "checklist",
      description: "Check local Arduino CLI and installed cores",
    },
    {
      id: "arduinoProject",
      label: "Select Project",
      command: "micropython.arduino.selectProjectFolder",
      icon: "folder-opened",
      description: "Choose the sketch folder to compile",
    },
    {
      id: "arduinoBoard",
      label: "Select Board",
      command: "micropython.arduino.selectBoard",
      icon: "circuit-board",
      description: "Set the board FQBN used by compile and upload",
    },
    {
      id: "arduinoInstallCore",
      label: "Install Core",
      command: "micropython.arduino.installCore",
      icon: "cloud-download",
      description: "Install a core package such as arduino:avr",
    },
    {
      id: "arduinoCompile",
      label: "Compile",
      command: "micropython.arduino.compile",
      icon: "gear",
      description: "Build the selected sketch for the selected board",
    },
    {
      id: "arduinoUpload",
      label: "Upload",
      command: "micropython.arduino.upload",
      icon: "cloud-upload",
      description: "Upload an already compiled sketch to the selected port",
    },
    {
      id: "arduinoCompileUpload",
      label: "Compile and Upload",
      command: "micropython.arduino.compileAndUpload",
      icon: "rocket",
      description: "Build and upload in one step",
    },
  ],
  espidf: [
    {
      id: "chooseFramework",
      label: "Choose Framework",
      command: "micropython.showPlatformLauncher",
      icon: "arrow-left",
    },
    {
      id: "espIdfStatus",
      label: "Toolchain Status",
      command: "micropython.espIdf.status",
      icon: "checklist",
    },
    {
      id: "espIdfSetTarget",
      label: "Set Target",
      command: "micropython.espIdf.setTarget",
      icon: "target",
    },
    {
      id: "espIdfBuild",
      label: "Build",
      command: "micropython.espIdf.build",
      icon: "gear",
    },
    {
      id: "espIdfFlash",
      label: "Flash",
      command: "micropython.espIdf.flash",
      icon: "cloud-upload",
    },
    {
      id: "espIdfBuildFlash",
      label: "Build and Flash",
      command: "micropython.espIdf.buildAndFlash",
      icon: "rocket",
    },
  ],
  stm: [
    {
      id: "chooseFramework",
      label: "Choose Framework",
      command: "micropython.showPlatformLauncher",
      icon: "arrow-left",
    },
    {
      id: "stmStatus",
      label: "ST-Link Status",
      command: "micropython.stm.stlinkStatus",
      icon: "checklist",
    },
    {
      id: "stmBuild",
      label: "Build Firmware",
      command: "micropython.stm.buildFirmware",
      icon: "gear",
    },
    {
      id: "stmFlash",
      label: "Flash Firmware",
      command: "micropython.stm.flashFirmware",
      icon: "cloud-upload",
    },
    {
      id: "stmBuildFlash",
      label: "Build and Flash",
      command: "micropython.stm.buildAndFlash",
      icon: "rocket",
    },
    {
      id: "stmErase",
      label: "Erase Chip",
      command: "micropython.stm.eraseChip",
      icon: "trash",
    },
  ],
};

class PlatformActionItem extends vscode.TreeItem {
  constructor(action: PlatformActionDefinition) {
    super(action.label, vscode.TreeItemCollapsibleState.None);
    this.id = action.id;
    this.description = action.description;
    this.tooltip = action.description ? `${action.label}\n${action.description}` : action.label;
    this.iconPath = new vscode.ThemeIcon(action.icon);
    this.contextValue = "platformAction";
    this.command = {
      command: action.command,
      title: action.label,
    };
  }
}

type EspIdfSection = "resources" | "actions";

class EspIdfSectionItem extends vscode.TreeItem {
  constructor(
    public readonly section: EspIdfSection,
    label: string,
    icon: string,
  ) {
    super(label, vscode.TreeItemCollapsibleState.Expanded);
    this.id = `espidf.${section}`;
    this.iconPath = new vscode.ThemeIcon(icon);
    this.contextValue = "espIdfSection";
  }
}

class EspIdfResourceItem extends vscode.TreeItem {
  constructor(
    id: string,
    label: string,
    resourcePath: string,
    installed: boolean,
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.id = id;
    this.description = installed ? "Installed" : "Missing";
    this.tooltip = `${label}\n${resourcePath}\n${this.description}`;
    this.iconPath = installed
      ? new vscode.ThemeIcon("check", new vscode.ThemeColor("testing.iconPassed"))
      : new vscode.ThemeIcon("cloud-download", new vscode.ThemeColor("charts.orange"));
    this.contextValue = installed ? "espIdfResourceInstalled" : "espIdfResourceMissing";
  }
}

type EspIdfTreeItem = EspIdfSectionItem | EspIdfResourceItem | PlatformActionItem;

export class EspIdfActionViewProvider implements vscode.TreeDataProvider<EspIdfTreeItem> {
  constructor(private readonly extensionPath: string) {}

  public getTreeItem(element: EspIdfTreeItem): vscode.TreeItem {
    return element;
  }

  public getChildren(element?: EspIdfTreeItem): EspIdfTreeItem[] {
    if (!element) {
      return [
        new EspIdfSectionItem("actions", "Actions", "tools"),
        new EspIdfSectionItem("resources", "Required Resources", "checklist"),
      ];
    }

    if (element instanceof EspIdfSectionItem && element.section === "resources") {
      return this.getResourceItems();
    }

    if (element instanceof EspIdfSectionItem && element.section === "actions") {
      return PLATFORM_ACTIONS.espidf.map((action) => new PlatformActionItem(action));
    }

    return [];
  }

  private getResourceItems(): EspIdfResourceItem[] {
    return [
      this.createResourceItem("espidf.framework", "ESP-IDF Framework", "toolchain/esp-idf"),
      this.createResourceItem("espidf.tools", "Espressif Tools", "toolchain/espressif"),
      this.createResourceItem("espidf.pythonRuntime", "Python Runtime", "runtime/linux-x64"),
    ];
  }

  private createResourceItem(id: string, label: string, relativePath: string): EspIdfResourceItem {
    const absolutePath = path.join(this.extensionPath, relativePath);
    return new EspIdfResourceItem(id, label, relativePath, fs.existsSync(absolutePath));
  }
}

export class PlatformLauncherViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "micropython.platformLauncherView";

  private webviewView: vscode.WebviewView | undefined;

  constructor(private readonly recentProjects: () => readonly PlatformRecentProject[]) {}

  public refresh(): void {
    if (!this.webviewView) {
      return;
    }

    try {
      this.webviewView.webview.html = this.renderHtml(this.webviewView.webview);
    } catch {
      this.webviewView = undefined;
    }
  }

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.webviewView = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
    };
    webviewView.webview.html = this.renderHtml(webviewView.webview);
    webviewView.onDidDispose(() => {
      if (this.webviewView === webviewView) {
        this.webviewView = undefined;
      }
    });

    webviewView.webview.onDidReceiveMessage((message: unknown) => {
      if (!message || typeof message !== "object") {
        return;
      }

      const candidate = message as {
        command?: unknown;
        platformId?: unknown;
        projectName?: unknown;
        parentFolder?: unknown;
        boardTarget?: unknown;
        port?: unknown;
        folder?: unknown;
      };
      if (candidate.command === "chooseLocation") {
        void this.chooseLocation(webviewView.webview);
        return;
      }
      if (candidate.command === "launchProject" && this.isPlatformId(candidate.platformId)) {
        const setup = this.toPlatformProjectSetup(candidate);
        if (setup) {
          void vscode.commands.executeCommand("micropython.launchNewPlatformProject", setup);
        }
        return;
      }
      if (
        candidate.command === "openRecentProject" &&
        this.isPlatformId(candidate.platformId) &&
        typeof candidate.folder === "string"
      ) {
        void vscode.commands.executeCommand("micropython.openRecentPlatformProject", candidate.platformId, candidate.folder);
      }
    });
  }

  private async chooseLocation(webview: vscode.Webview): Promise<void> {
    const picked = await vscode.window.showOpenDialog({
      canSelectFiles: false,
      canSelectFolders: true,
      canSelectMany: false,
      openLabel: "Use Location",
      title: "Project Setup: Select Location",
    });
    if (!picked || picked.length === 0) {
      return;
    }

    try {
      await webview.postMessage({ command: "locationSelected", folder: picked[0].fsPath });
    } catch {
      // The setup view may be hidden or disposed while the native folder picker is open.
    }
  }

  private toPlatformProjectSetup(candidate: {
    platformId?: unknown;
    projectName?: unknown;
    parentFolder?: unknown;
    boardTarget?: unknown;
    port?: unknown;
  }): PlatformProjectSetup | undefined {
    if (!this.isPlatformId(candidate.platformId)) {
      return undefined;
    }
    if (typeof candidate.projectName !== "string" || candidate.projectName.trim().length === 0) {
      return undefined;
    }
    if (typeof candidate.parentFolder !== "string" || candidate.parentFolder.trim().length === 0) {
      return undefined;
    }

    return {
      platformId: candidate.platformId,
      projectName: candidate.projectName.trim(),
      parentFolder: candidate.parentFolder.trim(),
      boardTarget: typeof candidate.boardTarget === "string" ? candidate.boardTarget.trim() : undefined,
      port: typeof candidate.port === "string" ? candidate.port.trim() : undefined,
    };
  }

  private isPlatformId(value: unknown): value is PlatformId {
    return value === "micropython" || value === "arduino" || value === "espidf" || value === "stm";
  }

  private renderHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    const recentProjects = this.recentProjects();
    const recentProjectsJson = JSON.stringify(recentProjects).replace(/</g, "\\u003c");
    const recentRows = recentProjects.length > 0
      ? recentProjects.map((project, index) => (
        `<button class="recent-row recent-row-${project.platformId}" type="button" data-index="${index}">
          <span class="badge badge-${project.platformId}">${escapeHtml(project.platformLabel)}</span>
          <span class="recent-main">
            <span class="recent-name">${escapeHtml(project.name)}</span>
            <span class="recent-folder">${escapeHtml(project.folder)}</span>
          </span>
          <span class="open-icon">Open</span>
        </button>`
      )).join("")
      : `<div class="empty">No recent projects yet</div>`;
    const platformOptions = PLATFORMS.map((platform) => (
      `<option value="${platform.id}">${escapeHtml(platform.label)}</option>`
    )).join("");

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {
      margin: 0;
      padding: 12px;
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
    }

    .setup {
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-width: 0;
    }

    .title {
      margin: 0 0 2px;
      font-size: 13px;
      font-weight: 700;
      line-height: 1.3;
    }

    .group {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
      opacity: 1;
      transition: opacity 120ms ease;
    }

    .group.disabled {
      opacity: 0.42;
    }

    label,
    .group-label,
    .section-title {
      color: var(--vscode-foreground);
      font-size: 11px;
      font-weight: 700;
      line-height: 1.3;
    }

    .field,
    select,
    input {
      width: 100%;
      min-width: 0;
      min-height: 28px;
      box-sizing: border-box;
      padding: 4px 8px;
      color: var(--vscode-input-foreground);
      background: var(--vscode-input-background);
      border: 1px solid var(--vscode-input-border, transparent);
      border-radius: 4px;
      font: inherit;
    }

    select:focus,
    input:focus,
    button:focus {
      outline: 1px solid var(--vscode-focusBorder);
      outline-offset: -1px;
    }

    input::placeholder {
      color: var(--vscode-input-placeholderForeground);
    }

    .location-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 6px;
      align-items: center;
    }

    .field {
      display: flex;
      align-items: center;
      overflow: hidden;
      color: var(--vscode-descriptionForeground);
      white-space: nowrap;
      text-overflow: ellipsis;
    }

    .secondary-button,
    .launch,
    .recent-row {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      border-radius: 4px;
      font: inherit;
      cursor: pointer;
    }

    .secondary-button {
      padding: 0 8px;
      color: var(--vscode-button-secondaryForeground);
      background: var(--vscode-button-secondaryBackground);
      border: 1px solid transparent;
    }

    .secondary-button:hover:not(:disabled) {
      background: var(--vscode-button-secondaryHoverBackground);
    }

    .launch {
      width: 100%;
      margin-top: 2px;
      padding: 0 10px;
      color: var(--vscode-button-foreground);
      background: var(--vscode-button-background);
      border: 1px solid transparent;
      font-weight: 700;
    }

    .launch:hover:not(:disabled) {
      background: var(--vscode-button-hoverBackground);
    }

    button:disabled,
    select:disabled,
    input:disabled {
      cursor: default;
    }

    .recent {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 4px;
      padding-top: 10px;
      border-top: 1px solid var(--vscode-sideBarSectionHeader-border, var(--vscode-widget-border));
    }

    .recent-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      width: 100%;
      min-height: 42px;
      padding: 6px 8px;
      color: var(--vscode-foreground);
      background: transparent;
      border: 1px solid var(--vscode-widget-border, transparent);
      text-align: left;
    }

    .recent-row:hover,
    .recent-row:focus {
      background: var(--vscode-list-hoverBackground);
    }

    .badge {
      align-self: center;
      max-width: 72px;
      padding: 2px 5px;
      overflow: hidden;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .badge-micropython {
      color: #ffffff;
      background: #2f7d32;
    }

    .badge-arduino {
      color: #ffffff;
      background: #007c89;
    }

    .badge-espidf {
      color: #ffffff;
      background: #b3261e;
    }

    .badge-stm {
      color: #ffffff;
      background: #3451b2;
    }

    .recent-row-micropython {
      border-left: 3px solid #2f7d32;
    }

    .recent-row-arduino {
      border-left: 3px solid #007c89;
    }

    .recent-row-espidf {
      border-left: 3px solid #b3261e;
    }

    .recent-row-stm {
      border-left: 3px solid #3451b2;
    }

    .recent-main {
      display: flex;
      flex-direction: column;
      min-width: 0;
      gap: 2px;
    }

    .recent-name,
    .recent-folder {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .recent-name {
      font-weight: 700;
    }

    .recent-folder,
    .empty,
    .open-icon {
      color: var(--vscode-descriptionForeground);
      font-size: 11px;
    }

    .open-icon {
      align-self: center;
    }
  </style>
</head>
<body>
  <form class="setup">
    <h2 class="title">Project Setup</h2>

    <div class="group">
      <label for="framework">Framework</label>
      <select id="framework">
        <option value="">Select framework</option>
        ${platformOptions}
      </select>
    </div>

    <div class="group disabled" id="projectGroup">
      <span class="group-label">New Project</span>
      <input id="projectName" type="text" placeholder="Project name" disabled>
      <div class="location-row">
        <span class="field" id="locationValue">Location</span>
        <button class="secondary-button" id="chooseLocation" type="button" disabled>Browse</button>
      </div>
    </div>

    <div class="group disabled" id="targetGroup">
      <label id="targetLabel" for="boardTarget">Board / Target</label>
      <input id="boardTarget" type="search" list="targetOptions" placeholder="Search board or target" disabled>
      <datalist id="targetOptions"></datalist>
    </div>

    <button class="launch" id="launch" type="button" disabled>Launch Framework</button>

    <section class="recent" aria-label="Recent Projects">
      <span class="section-title">Recent Projects</span>
      ${recentRows}
    </section>
  </form>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const recentProjects = ${recentProjectsJson};
    const framework = document.getElementById("framework");
    const projectGroup = document.getElementById("projectGroup");
    const projectName = document.getElementById("projectName");
    const locationValue = document.getElementById("locationValue");
    const chooseLocation = document.getElementById("chooseLocation");
    const targetGroup = document.getElementById("targetGroup");
    const targetLabel = document.getElementById("targetLabel");
    const boardTarget = document.getElementById("boardTarget");
    const targetOptionsList = document.getElementById("targetOptions");
    const launch = document.getElementById("launch");
    let parentFolder = "";

    const projectDefaults = {
      micropython: "micropython_app",
      arduino: "arduino_sketch",
      espidf: "esp_idf_app",
      stm: "stm_baremetal_app",
    };
    const targetOptions = {
      micropython: [{ label: "Not required", value: "" }],
      arduino: [
        { label: "Arduino Uno", value: "arduino:avr:uno" },
        { label: "Arduino Nano", value: "arduino:avr:nano" },
        { label: "Arduino Mega 2560", value: "arduino:avr:mega" },
        { label: "Arduino Leonardo", value: "arduino:avr:leonardo" },
        { label: "Arduino Micro", value: "arduino:avr:micro" },
        { label: "Arduino Pro Mini", value: "arduino:avr:pro" },
      ],
      espidf: [
        { label: "ESP32", value: "esp32" },
        { label: "ESP32-S3", value: "esp32s3" },
        { label: "ESP32-C3", value: "esp32c3" },
      ],
      stm: [
        { label: "STM32F0", value: "stm32f0" },
        { label: "STM32F1", value: "stm32f1" },
        { label: "STM32F4", value: "stm32f4" },
      ],
    };

    function needsTarget(platformId) {
      return platformId === "arduino" || platformId === "espidf" || platformId === "stm";
    }

    function setTargetOptions(platformId) {
      const options = targetOptions[platformId] || [];
      boardTarget.value = "";
      targetOptionsList.textContent = "";
      for (const option of options) {
        const item = document.createElement("option");
        item.value = option.value;
        item.label = option.label;
        targetOptionsList.appendChild(item);
      }
      if (platformId === "arduino") {
        targetLabel.textContent = "Board";
      } else if (platformId === "espidf" || platformId === "stm") {
        targetLabel.textContent = "Target";
      } else {
        targetLabel.textContent = "Board / Target";
      }
    }

    function setGroupEnabled(group, enabled) {
      group.classList.toggle("disabled", !enabled);
    }

    function updateState() {
      const platformId = framework.value;
      const hasFramework = Boolean(platformId);
      const hasProject = projectName.value.trim().length > 0 && Boolean(parentFolder);
      const targetRequired = needsTarget(platformId);
      const targetReady = !targetRequired || boardTarget.value.trim().length > 0;
      const targetEnabled = hasFramework && hasProject;

      setGroupEnabled(projectGroup, hasFramework);
      projectName.disabled = !hasFramework;
      chooseLocation.disabled = !hasFramework;

      setGroupEnabled(targetGroup, targetEnabled);
      boardTarget.disabled = !targetEnabled || !targetRequired;

      launch.disabled = !(hasFramework && hasProject && targetReady);
    }

    framework.addEventListener("change", () => {
      const platformId = framework.value;
      setTargetOptions(platformId);
      if (platformId && !projectName.value.trim()) {
        projectName.value = projectDefaults[platformId] || "";
      }
      updateState();
    });
    projectName.addEventListener("input", updateState);
    boardTarget.addEventListener("input", updateState);
    chooseLocation.addEventListener("click", () => {
      vscode.postMessage({ command: "chooseLocation" });
    });
    launch.addEventListener("click", () => {
      vscode.postMessage({
        command: "launchProject",
        platformId: framework.value,
        projectName: projectName.value,
        parentFolder,
        boardTarget: boardTarget.value.trim(),
      });
    });

    for (const row of document.querySelectorAll(".recent-row")) {
      row.addEventListener("click", () => {
        const project = recentProjects[Number(row.dataset.index)];
        if (!project) {
          return;
        }
        vscode.postMessage({
          command: "openRecentProject",
          platformId: project.platformId,
          folder: project.folder,
        });
      });
    }

    window.addEventListener("message", (event) => {
      const message = event.data;
      if (!message || message.command !== "locationSelected") {
        return;
      }
      parentFolder = message.folder || "";
      locationValue.textContent = parentFolder || "Location";
      locationValue.title = parentFolder;
      updateState();
    });

    setTargetOptions("");
    updateState();
  </script>
</body>
</html>`;
  }
}

export class PlatformProjectViewProvider implements vscode.WebviewViewProvider {
  constructor(private readonly platformId: PlatformId, private readonly platformLabel: string) {}

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    webviewView.webview.options = {
      enableScripts: true,
    };
    webviewView.webview.html = this.renderHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage((message: unknown) => {
      if (!message || typeof message !== "object") {
        return;
      }

      const candidate = message as { command?: unknown };
      if (candidate.command === "createProject") {
        void vscode.commands.executeCommand("micropython.createPlatformProject", this.platformId);
      }
      if (candidate.command === "openProject") {
        void vscode.commands.executeCommand("micropython.openPlatformProject", this.platformId);
      }
      if (candidate.command === "chooseFramework") {
        void vscode.commands.executeCommand("micropython.showPlatformLauncher");
      }
    });
  }

  private renderHtml(webview: vscode.Webview): string {
    const nonce = getNonce();
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {
      margin: 0;
      padding: 0;
      color: var(--vscode-foreground);
      background: var(--vscode-sideBar-background);
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
    }

    .option {
      display: flex;
      align-items: center;
      width: 100%;
      min-height: 34px;
      padding: 0 12px;
      color: var(--vscode-foreground);
      background: transparent;
      border: 0;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }

    .option:hover,
    .option:focus {
      background: var(--vscode-list-hoverBackground);
      outline: none;
    }

    .option:focus {
      outline: 1px solid var(--vscode-focusBorder);
      outline-offset: -1px;
    }

    .icon {
      width: 20px;
      margin-right: 8px;
      color: var(--vscode-descriptionForeground);
      font-size: 18px;
      line-height: 1;
      text-align: center;
    }

    .label {
      min-width: 0;
      overflow: hidden;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  </style>
</head>
<body>
  <button class="option" type="button" data-command="createProject">
    <span class="icon">+</span>
    <span class="label">Create ${this.platformLabel} project</span>
  </button>
  <button class="option" type="button" data-command="openProject">
    <span class="icon">&gt;</span>
    <span class="label">Open existing project</span>
  </button>
  <button class="option" type="button" data-command="chooseFramework">
    <span class="icon">&lt;</span>
    <span class="label">Choose framework</span>
  </button>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    for (const row of document.querySelectorAll(".option")) {
      row.addEventListener("click", () => {
        vscode.postMessage({ command: row.dataset.command });
      });
    }
  </script>
</body>
</html>`;
  }
}

export class PlatformActionViewProvider implements vscode.TreeDataProvider<PlatformActionItem> {
  constructor(private readonly platformId: PlatformId) {}

  public getTreeItem(element: PlatformActionItem): vscode.TreeItem {
    return element;
  }

  public getChildren(element?: PlatformActionItem): PlatformActionItem[] {
    if (element) {
      return [];
    }
    return PLATFORM_ACTIONS[this.platformId].map((action) => new PlatformActionItem(action));
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function getNonce(): string {
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  for (let index = 0; index < 32; index += 1) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}
