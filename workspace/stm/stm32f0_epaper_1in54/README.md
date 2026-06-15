# STM32F0 Waveshare 1.54 e-Paper Test

Memory-tight 200x200 e-paper test for STM32F0. It uses software SPI and streams display bytes directly, without a full framebuffer.

## Pin Map

```text
DIN  -> PA5
CLK  -> PA6
CS   -> PA7
DC   -> PB1
RST  -> PA9
BUSY -> PA10
VCC  -> 3.3V or module-supported VCC
GND  -> GND
```

## Current Demo

The firmware initializes the panel, streams a dithered 200x200 black/white `sohoxconnect` image directly to display RAM, refreshes, then puts the e-paper controller to sleep. The bitmap is packed in `include/sohoxconnect_image.h` and avoids allocating a full framebuffer.
