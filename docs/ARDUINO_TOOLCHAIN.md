# Arduino Toolchain

Arduino support uses a local ignored toolchain cache instead of bundling large packages in the VSIX.

```text
toolchain/arduino/bin/arduino-cli
toolchain/arduino/arduino-cli.yaml
toolchain/arduino/data
toolchain/arduino/downloads
toolchain/arduino/user
```

Install Arduino CLI:

```bash
npm run install-arduino-toolchain
```

Install a core only when needed. For Arduino Uno/Nano-style AVR boards:

```bash
toolchain/arduino/bin/arduino-cli --config-file toolchain/arduino/arduino-cli.yaml core install arduino:avr
```

Example local project:

```text
workspace/arduino/blink/blink.ino
```

Example compile:

```bash
toolchain/arduino/bin/arduino-cli --config-file toolchain/arduino/arduino-cli.yaml compile --fqbn arduino:avr:uno workspace/arduino/blink
```

The extension backend exposes headless tools for status, core install, compile, upload, and compile/upload.
