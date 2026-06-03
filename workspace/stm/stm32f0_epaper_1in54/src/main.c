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

static uint32_t in_rect(uint32_t x, uint32_t y, uint32_t left, uint32_t top, uint32_t width, uint32_t height) {
    return x >= left && x < (left + width) && y >= top && y < (top + height);
}

static uint32_t in_line_band(uint32_t x, uint32_t y, uint32_t x0, uint32_t y0, uint32_t x1, uint32_t y1, uint32_t thickness) {
    int32_t px = (int32_t)x;
    int32_t py = (int32_t)y;
    int32_t ax = (int32_t)x0;
    int32_t ay = (int32_t)y0;
    int32_t bx = (int32_t)x1;
    int32_t by = (int32_t)y1;
    int32_t vx = bx - ax;
    int32_t vy = by - ay;
    int32_t wx = px - ax;
    int32_t wy = py - ay;
    int32_t len_sq = vx * vx + vy * vy;
    int32_t projection = wx * vx + wy * vy;

    if (projection < 0 || projection > len_sq) {
        return 0u;
    }

    int32_t cross = wx * vy - wy * vx;
    if (cross < 0) {
        cross = -cross;
    }

    return (uint32_t)(cross * cross) <= (thickness * thickness * (uint32_t)len_sq);
}

static uint32_t below_line(uint32_t x, uint32_t y, uint32_t x0, uint32_t y0, uint32_t x1, uint32_t y1) {
    int32_t px = (int32_t)x;
    int32_t py = (int32_t)y;
    int32_t ax = (int32_t)x0;
    int32_t ay = (int32_t)y0;
    int32_t bx = (int32_t)x1;
    int32_t by = (int32_t)y1;
    int32_t side = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
    return side >= 0;
}

static uint32_t in_triangle(uint32_t x, uint32_t y, uint32_t ax, uint32_t ay, uint32_t bx, uint32_t by, uint32_t cx, uint32_t cy) {
    uint32_t b1 = below_line(x, y, ax, ay, bx, by);
    uint32_t b2 = below_line(x, y, bx, by, cx, cy);
    uint32_t b3 = below_line(x, y, cx, cy, ax, ay);
    return (b1 == b2) && (b2 == b3);
}

static uint32_t checker(uint32_t x, uint32_t y, uint32_t size) {
    return (((x / size) + (y / size)) & 1u) != 0u;
}

static uint32_t tree_pixel(uint32_t x, uint32_t y, uint32_t cx, uint32_t base_y, uint32_t height) {
    uint32_t trunk_w = height / 8u;
    uint32_t trunk_h = height / 3u;
    if (in_rect(x, y, cx - trunk_w / 2u, base_y - trunk_h, trunk_w + 1u, trunk_h)) {
        return 1u;
    }

    return in_triangle(x, y, cx, base_y - height, cx - height / 3u, base_y - trunk_h / 2u, cx + height / 3u, base_y - trunk_h / 2u) ||
           in_triangle(x, y, cx, base_y - height + height / 4u, cx - height / 3u, base_y - trunk_h / 4u, cx + height / 3u, base_y - trunk_h / 4u);
}

static uint32_t scenery_pixel(uint32_t x, uint32_t y) {
    if (x < 4u || x >= 196u || y < 4u || y >= 196u) {
        return 1u;
    }

    if (in_disc(x, y, 160u, 34u, 16u) || in_ring(x, y, 160u, 34u, 23u, 2u)) {
        return 1u;
    }

    if (in_ellipse(x, y, 48u, 38u, 18u, 7u) ||
        in_ellipse(x, y, 63u, 35u, 15u, 8u) ||
        in_ellipse(x, y, 78u, 39u, 20u, 7u) ||
        in_ellipse(x, y, 114u, 54u, 15u, 6u) ||
        in_ellipse(x, y, 128u, 51u, 13u, 7u) ||
        in_ellipse(x, y, 142u, 54u, 16u, 6u)) {
        return 1u;
    }

    if (in_triangle(x, y, 8u, 120u, 58u, 58u, 112u, 120u) ||
        in_triangle(x, y, 70u, 122u, 126u, 70u, 190u, 122u)) {
        return 1u;
    }

    if (in_triangle(x, y, 48u, 70u, 58u, 58u, 70u, 72u) ||
        in_triangle(x, y, 114u, 82u, 126u, 70u, 139u, 83u)) {
        return 0u;
    }

    if (in_line_band(x, y, 4u, 122u, 196u, 122u, 2u)) {
        return 1u;
    }

    if (y > 122u && y < 168u) {
        if (in_line_band(x, y, 10u, 135u, 190u, 128u, 1u) ||
            in_line_band(x, y, 0u, 151u, 200u, 143u, 1u) ||
            in_line_band(x, y, 18u, 162u, 182u, 156u, 1u)) {
            return 1u;
        }
        return checker(x + y, y, 7u) && y > 132u;
    }

    if (in_rect(x, y, 124u, 136u, 42u, 34u) ||
        in_triangle(x, y, 118u, 136u, 145u, 116u, 172u, 136u) ||
        in_rect(x, y, 139u, 150u, 10u, 20u) ||
        in_rect(x, y, 153u, 145u, 8u, 8u)) {
        return 1u;
    }

    if (in_line_band(x, y, 22u, 186u, 178u, 170u, 2u) ||
        in_line_band(x, y, 35u, 193u, 138u, 168u, 2u)) {
        return 1u;
    }

    if (tree_pixel(x, y, 28u, 178u, 46u) ||
        tree_pixel(x, y, 53u, 174u, 36u) ||
        tree_pixel(x, y, 181u, 177u, 42u)) {
        return 1u;
    }

    return 0u;
}

static uint8_t scenery_byte(uint32_t x_byte, uint32_t y) {
    uint8_t value = 0xFFu;
    uint32_t base_x = x_byte * 8u;
    for (uint32_t bit = 0; bit < 8u; bit++) {
        if (scenery_pixel(base_x + bit, y)) {
            value &= (uint8_t)~(0x80u >> bit);
        }
    }
    return value;
}

static void epd_write_scenery(void) {
    epd_send_command(0x24u);
    for (uint32_t y = 0; y < EPD_HEIGHT; y++) {
        for (uint32_t x_byte = 0; x_byte < EPD_BYTES_PER_ROW; x_byte++) {
            epd_send_data(scenery_byte(x_byte, y));
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
    epd_write_scenery();
    epd_refresh();
    epd_sleep();

    while (1) {
        delay(100000u);
    }
}
