#!/usr/bin/env node
"use strict";

const path = require("path");
const { spawnSync } = require("child_process");

const stageScript = path.join(__dirname, "stage_runtime.py");
const extraArgs = process.argv.slice(2);
const candidates = process.platform === "win32"
  ? [
      { command: "py", args: ["-3"] },
      { command: "python", args: [] },
      { command: "python3", args: [] },
    ]
  : [
      { command: "python3", args: [] },
      { command: "python", args: [] },
    ];

for (const candidate of candidates) {
  const result = spawnSync(candidate.command, [...candidate.args, stageScript, ...extraArgs], {
    stdio: "inherit",
    shell: false,
  });

  if (result.error && result.error.code === "ENOENT") {
    continue;
  }

  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }

  if (result.signal) {
    console.error(`Runtime staging stopped by signal ${result.signal}.`);
    process.exit(1);
  }

  process.exit(result.status ?? 1);
}

console.error("Could not find Python. Install Python only on the packaging machine, then rerun npm run stage-runtime.");
process.exit(1);
