# STM32 ST-Link Runtime Support

This extension now has a headless backend path for STM32 programming through ST-Link. It is intentionally backend-first: no VS Code commands or UI are required yet, so the extension definition can change later without rewriting the flashing logic.

## Runtime Layout

For a distributable Linux x64 build, place ST-Link/OpenOCD tools inside the bundled runtime:

```text
runtime/linux-x64/bin/openocd
runtime/linux-x64/bin/st-info
runtime/linux-x64/bin/st-flash
runtime/linux-x64/share/openocd/scripts/interface/stlink.cfg
runtime/linux-x64/share/openocd/scripts/target/stm32f4x.cfg
runtime/linux-x64/share/stlink/chips
runtime/linux-x64/lib/udev/rules.d
```

The extension already prepends `runtime/<platform>/bin` to `PATH` for backend processes. The backend also discovers OpenOCD scripts at:

```text
runtime/<platform>/share/openocd/scripts
```

For local development, system-installed tools also work if they are on `PATH`.

The Linux runtime currently bundles OpenOCD `0.12.0` and stlink-tools `1.8.0` from Ubuntu Noble packages, plus the shared libraries required by those binaries.

To recreate this local bundle on a Linux packaging machine:

```bash
npm run stage-stlink-runtime
```

## Headless Tools

The MCP backend exposes these STM tools:

```text
stm_stlink_status
stm_stlink_flash
stm_stlink_erase
stm_build_firmware
stm_build_and_flash
```

`stm_stlink_status` checks for `openocd`, `st-info`, `st-flash`, OpenOCD scripts, and attached ST-Link USB devices.

`stm_stlink_flash` programs `.bin`, `.hex`, or `.elf` firmware through OpenOCD. For `.bin` files it uses flash address `0x08000000` by default. For `.hex` and `.elf`, OpenOCD uses addresses from the file.

`stm_stlink_erase` erases flash bank 0 through OpenOCD.

`stm_build_firmware` builds a local STM32 firmware project with the ignored local ARM toolchain and emits `.elf`, `.bin`, and `.map` artifacts.

`stm_build_and_flash` builds a local STM32 firmware project, then flashes the generated `.bin` through ST-Link/OpenOCD.

## Target Values

Use a target family key or a direct OpenOCD target config path:

```text
stm32f1 -> target/stm32f1x.cfg
stm32f4 -> target/stm32f4x.cfg
stm32g0 -> target/stm32g0x.cfg
stm32h7 -> target/stm32h7x.cfg
target/stm32f4x.cfg
```

Supported family keys are defined in:

```text
src/backend/host/python_service/stlink.py
```

## Example Commands

Probe tool/runtime status:

```json
{
  "name": "stm_stlink_status",
  "arguments": {}
}
```

Flash a MicroPython STM32 firmware binary:

```json
{
  "name": "stm_stlink_flash",
  "arguments": {
    "target": "stm32f4",
    "firmwarePath": "/absolute/path/firmware.bin",
    "address": "0x08000000",
    "verify": true,
    "reset": true
  }
}
```

Erase a chip:

```json
{
  "name": "stm_stlink_erase",
  "arguments": {
    "target": "stm32f4"
  }
}
```

Build the included STM32F0 PA6 LED test firmware:

```json
{
  "name": "stm_build_firmware",
  "arguments": {
    "projectFolder": "workspace/stm/stm32f0_pa6_led",
    "target": "stm32f0"
  }
}
```

Build and flash the PA6 LED test firmware:

```json
{
  "name": "stm_build_and_flash",
  "arguments": {
    "projectFolder": "workspace/stm/stm32f0_pa6_led",
    "target": "stm32f0",
    "verify": true,
    "reset": true
  }
}
```

## Linux USB Permission

Bundling binaries does not grant USB permission. Linux users still need udev rules for ST-Link. For the currently detected ST-LINK/V2, the USB ID is:

```text
0483:3748
```

After adding udev rules, reload them and reconnect ST-Link.

Bundled udev rule files are available here:

```text
runtime/linux-x64/lib/udev/rules.d
```

For local development on Ubuntu, copy them into the system udev rules directory, reload rules, then reconnect ST-Link:

```bash
sudo cp runtime/linux-x64/lib/udev/rules.d/* /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Without this step, OpenOCD may be present but fail with:

```text
LIBUSB_ERROR_ACCESS
```

## Scope

ST-Link is used for flashing and erase operations. It does not provide the normal MicroPython REPL or filesystem protocol. After flashing MicroPython firmware, serial USB/UART is still needed for file sync, terminal, and REPL workflows.

## Local ARM Toolchain

Do not bundle the full ARM GCC toolchain in the VSIX. It is too large for normal extension distribution.

For local STM32 firmware builds, install the toolchain into the ignored `toolchain/` folder:

```bash
npm run install-arm-toolchain
```

This installs C firmware support under:

```text
toolchain/arm-none-eabi/bin/arm-none-eabi-gcc
toolchain/arm-none-eabi/bin/arm-none-eabi-objcopy
toolchain/arm-none-eabi/bin/arm-none-eabi-size
```

The default install intentionally skips C++ newlib support because it adds roughly 2 GB. If C++ firmware support is required:

```bash
python3 scripts/install_arm_toolchain.py --include-cxx
```
