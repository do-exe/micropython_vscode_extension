from __future__ import annotations

from serial.tools import list_ports

MICROPYTHON_PRODUCT = "MicroPython"
DETECTED_PORT_KEYWORDS = ("Espressif", MICROPYTHON_PRODUCT)


def scan_microcontroller_ports() -> list[str]:
    strict_ports: list[str] = []
    fallback_ports: list[str] = []
    for port in list_ports.comports():
        device = str(port.device or "")
        text = f"{port.manufacturer or ''} {port.product or ''} {port.description or ''}".lower()
        vid = getattr(port, "vid", None)
        if any(keyword.lower() in text for keyword in DETECTED_PORT_KEYWORDS) or vid == 0x303A:
            if device:
                strict_ports.append(device)
            continue
        if device.startswith("/dev/ttyACM") or device.startswith("/dev/ttyUSB") or device.upper().startswith("COM"):
            fallback_ports.append(device)

    ordered: list[str] = []
    seen: set[str] = set()
    for device in strict_ports + fallback_ports:
        if device and device not in seen:
            seen.add(device)
            ordered.append(device)
    return ordered


def list_detected_microcontroller_ports() -> list[dict[str, str]]:
    current_ports = {str(port.device or ""): port for port in list_ports.comports()}
    devices: list[dict[str, str]] = []

    for device in scan_microcontroller_ports():
        port = current_ports.get(device)
        product = ""
        description = ""
        if port is not None:
            product = (port.product or port.manufacturer or "").strip()
            description = (port.description or "").strip()
        devices.append(
            {
                "port": device,
                "product": product,
                "description": description,
            }
        )

    return devices
