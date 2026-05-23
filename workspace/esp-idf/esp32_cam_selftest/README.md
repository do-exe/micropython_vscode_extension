# ESP32-CAM Self-Test

ESP-IDF self-test firmware for AI Thinker style ESP32-CAM boards.

The firmware initializes the camera and captures one RGB565 QQVGA frame every three seconds, logging frame size over UART.

## Upload

Use manual bootloader mode for this board:

```text
IO0 -> GND
press/release RESET
flash
remove IO0 from GND
press/release RESET
```

Port used during testing:

```text
/dev/ttyUSB0
```

## Confirmed Live Output

```text
Frame captured: 160x120, 38400 bytes, format=0
```
