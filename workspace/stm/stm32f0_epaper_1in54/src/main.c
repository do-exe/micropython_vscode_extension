#include <stdint.h>

#define RCC_AHBENR   (*(volatile uint32_t *)0x40021014u)
#define GPIOA_MODER  (*(volatile uint32_t *)0x48000000u)
#define GPIOA_IDR    (*(volatile uint32_t *)0x48000010u)
#define GPIOA_BSRR   (*(volatile uint32_t *)0x48000018u)
#define GPIOB_MODER  (*(volatile uint32_t *)0x48000400u)
#define GPIOB_BSRR   (*(volatile uint32_t *)0x48000418u)

#define RCC_AHBENR_GPIOAEN (1u << 17)
#define RCC_AHBENR_GPIOBEN (1u << 18)

#define PIN_DIN   5u
#define PIN_CLK   6u
#define PIN_CS    7u
#define PIN_DC    1u
#define PIN_RST   9u
#define PIN_BUSY 10u

#define EPD_WIDTH  200u
#define EPD_HEIGHT 200u
#define EPD_BYTES_PER_ROW (EPD_WIDTH / 8u)

static void delay(volatile uint32_t ticks) {
    while (ticks--) {
        __asm volatile ("nop");
    }
}

static void gpioa_high(uint32_t pin) {
    GPIOA_BSRR = (1u << pin);
}

static void gpioa_low(uint32_t pin) {
    GPIOA_BSRR = (1u << (pin + 16u));
}

static void gpiob_high(uint32_t pin) {
    GPIOB_BSRR = (1u << pin);
}

static void gpiob_low(uint32_t pin) {
    GPIOB_BSRR = (1u << (pin + 16u));
}

static uint32_t busy_is_high(void) {
    return (GPIOA_IDR & (1u << PIN_BUSY)) != 0u;
}

static void spi_write_byte(uint8_t value) {
    for (uint32_t bit = 0; bit < 8u; bit++) {
        if (value & 0x80u) {
            gpioa_high(PIN_DIN);
        } else {
            gpioa_low(PIN_DIN);
        }
        gpioa_high(PIN_CLK);
        delay(3u);
        gpioa_low(PIN_CLK);
        value <<= 1;
    }
}

static void epd_send_command(uint8_t command) {
    gpioa_low(PIN_CS);
    gpiob_low(PIN_DC);
    spi_write_byte(command);
    gpioa_high(PIN_CS);
}

static void epd_send_data(uint8_t data) {
    gpioa_low(PIN_CS);
    gpiob_high(PIN_DC);
    spi_write_byte(data);
    gpioa_high(PIN_CS);
}

static void epd_wait_until_idle(void) {
    uint32_t timeout = 8000000u;
    while (busy_is_high() && timeout--) {
        delay(10u);
    }
}

static void epd_reset(void) {
    gpioa_high(PIN_RST);
    delay(30000u);
    gpioa_low(PIN_RST);
    delay(30000u);
    gpioa_high(PIN_RST);
    delay(30000u);
}

static void epd_init(void) {
    epd_reset();
    epd_wait_until_idle();

    epd_send_command(0x01u);
    epd_send_data(0xC7u);
    epd_send_data(0x00u);
    epd_send_data(0x01u);

    epd_send_command(0x11u);
    epd_send_data(0x01u);

    epd_send_command(0x44u);
    epd_send_data(0x00u);
    epd_send_data(0x18u);

    epd_send_command(0x45u);
    epd_send_data(0xC7u);
    epd_send_data(0x00u);
    epd_send_data(0x00u);
    epd_send_data(0x00u);

    epd_send_command(0x3Cu);
    epd_send_data(0x01u);

    epd_send_command(0x18u);
    epd_send_data(0x80u);

    epd_send_command(0x4Eu);
    epd_send_data(0x00u);
    epd_send_command(0x4Fu);
    epd_send_data(0xC7u);
    epd_send_data(0x00u);
    epd_wait_until_idle();
}

static uint32_t abs_delta(uint32_t a, uint32_t b) {
    return a > b ? a - b : b - a;
}

static uint32_t in_disc(uint32_t x, uint32_t y, uint32_t cx, uint32_t cy, uint32_t radius) {
    uint32_t dx = abs_delta(x, cx);
    uint32_t dy = abs_delta(y, cy);
    return (dx * dx + dy * dy) <= (radius * radius);
}

static uint32_t in_ring(uint32_t x, uint32_t y, uint32_t cx, uint32_t cy, uint32_t radius, uint32_t thickness) {
    uint32_t dx = abs_delta(x, cx);
    uint32_t dy = abs_delta(y, cy);
    uint32_t distance_sq = dx * dx + dy * dy;
    uint32_t outer = radius * radius;
    uint32_t inner_radius = radius > thickness ? radius - thickness : 0u;
    uint32_t inner = inner_radius * inner_radius;
    return distance_sq <= outer && distance_sq >= inner;
}

static uint32_t in_ellipse(uint32_t x, uint32_t y, uint32_t cx, uint32_t cy, uint32_t rx, uint32_t ry) {
    uint32_t dx = abs_delta(x, cx);
    uint32_t dy = abs_delta(y, cy);
    return (dx * dx * ry * ry + dy * dy * rx * rx) <= (rx * rx * ry * ry);
}

static uint32_t smile_face_pixel(uint32_t x, uint32_t y) {
    if (in_ring(x, y, 100u, 100u, 78u, 5u)) {
        return 1u;
    }

    if (in_ellipse(x, y, 72u, 82u, 10u, 14u) || in_ellipse(x, y, 128u, 82u, 10u, 14u)) {
        return 1u;
    }

    if (in_disc(x, y, 75u, 78u, 3u) || in_disc(x, y, 131u, 78u, 3u)) {
        return 1u;
    }

    if (x >= 54u && x <= 146u && y >= 112u && y <= 154u) {
        uint32_t dx = abs_delta(x, 100u);
        uint32_t curve_y = 150u - ((dx * dx) >> 6);
        if (abs_delta(y, curve_y) <= 4u) {
            return 1u;
        }
    }

    return 0u;
}

static uint8_t smile_face_byte(uint32_t x_byte, uint32_t y) {
    uint8_t value = 0xFFu;
    uint32_t base_x = x_byte * 8u;
    for (uint32_t bit = 0; bit < 8u; bit++) {
        if (smile_face_pixel(base_x + bit, y)) {
            value &= (uint8_t)~(0x80u >> bit);
        }
    }
    return value;
}

static void epd_write_smile_face(void) {
    epd_send_command(0x24u);
    for (uint32_t y = 0; y < EPD_HEIGHT; y++) {
        for (uint32_t x_byte = 0; x_byte < EPD_BYTES_PER_ROW; x_byte++) {
            epd_send_data(smile_face_byte(x_byte, y));
        }
    }
}

static void epd_refresh(void) {
    epd_send_command(0x22u);
    epd_send_data(0xF7u);
    epd_send_command(0x20u);
    epd_wait_until_idle();
}

static void epd_sleep(void) {
    epd_send_command(0x10u);
    epd_send_data(0x01u);
}

static void gpio_init(void) {
    RCC_AHBENR |= RCC_AHBENR_GPIOAEN | RCC_AHBENR_GPIOBEN;

    GPIOA_MODER &= ~(
        (3u << (PIN_DIN * 2u)) |
        (3u << (PIN_CLK * 2u)) |
        (3u << (PIN_CS * 2u)) |
        (3u << (PIN_RST * 2u)) |
        (3u << (PIN_BUSY * 2u))
    );
    GPIOA_MODER |=
        (1u << (PIN_DIN * 2u)) |
        (1u << (PIN_CLK * 2u)) |
        (1u << (PIN_CS * 2u)) |
        (1u << (PIN_RST * 2u));

    GPIOB_MODER &= ~(3u << (PIN_DC * 2u));
    GPIOB_MODER |=  (1u << (PIN_DC * 2u));

    gpioa_low(PIN_CLK);
    gpioa_high(PIN_CS);
    gpioa_high(PIN_RST);
    gpiob_low(PIN_DC);
}

int main(void) {
    gpio_init();
    epd_init();
    epd_write_smile_face();
    epd_refresh();
    epd_sleep();

    while (1) {
        delay(100000u);
    }
}
