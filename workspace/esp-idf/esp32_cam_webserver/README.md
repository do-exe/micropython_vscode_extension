# ESP32-CAM Webserver

ESP-IDF firmware for the AI Thinker ESP32-CAM module.

## Network

- Wi-Fi mode: SoftAP
- SSID: `ESP32-CAM`
- Password: `12345678`
- URL after connecting to the AP: `http://192.168.4.1/`

## Endpoints

- `/` small status page
- `/capture` captures one JPEG frame
- `/stream` MJPEG live stream

## Flash Notes

If auto bootloader mode does not work on the board:

1. Connect `IO0` to `GND`.
2. Press and release reset.
3. Flash the firmware.
4. Remove `IO0` from `GND`.
5. Press reset again.

## Pin Map

AI Thinker ESP32-CAM:

| Signal | GPIO |
| --- | ---: |
| PWDN | 32 |
| RESET | -1 |
| XCLK | 0 |
| SIOD | 26 |
| SIOC | 27 |
| D7 | 35 |
| D6 | 34 |
| D5 | 39 |
| D4 | 36 |
| D3 | 21 |
| D2 | 19 |
| D1 | 18 |
| D0 | 5 |
| VSYNC | 25 |
| HREF | 23 |
| PCLK | 22 |
