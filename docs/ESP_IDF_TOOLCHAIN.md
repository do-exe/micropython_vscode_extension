# ESP-IDF Toolchain

ESP-IDF support uses an extension-local, ignored toolchain copy.

```text
toolchain/esp-idf
toolchain/espressif
```

This is a real copy, not a symlink. It is intentionally not packaged in the VSIX.

Install or refresh from the known local CalSci firmware setup:

```bash
npm run install-esp-idf-toolchain
```

Validate manually:

```bash
IDF_TOOLS_PATH="$PWD/toolchain/espressif" \
bash -lc 'source toolchain/esp-idf/export.sh && idf.py --version'
```

Template:

```text
templates/esp-idf/basic_app
```

Workspace example:

```text
workspace/esp-idf/basic_app
```

The backend exposes headless ESP-IDF MCP tools for status, set-target, build, flash, and build/flash.
