#include <stdint.h>

#define RCC_AHBENR   (*(volatile uint32_t *)0x40021014u)
#define GPIOA_MODER  (*(volatile uint32_t *)0x48000000u)
#define GPIOA_BSRR   (*(volatile uint32_t *)0x48000018u)

#define RCC_AHBENR_GPIOAEN (1u << 17)
#define LED_PIN 6u
#define PWM_TOP 255u
#define FADE_STEPS 256u

static void delay(volatile uint32_t ticks) {
    while (ticks--) {
        __asm volatile ("nop");
    }
}

static void led_on(void) {
    GPIOA_BSRR = (1u << LED_PIN);
}

static void led_off(void) {
    GPIOA_BSRR = (1u << (LED_PIN + 16u));
}

static void pwm_frame(uint32_t brightness) {
    uint32_t on_ticks = (brightness * brightness) >> 8;
    uint32_t off_ticks = PWM_TOP - on_ticks;

    if (on_ticks) {
        led_on();
        delay(on_ticks * 5u);
    }

    if (off_ticks) {
        led_off();
        delay(off_ticks * 5u);
    }
}

static void fade_to(uint32_t start, uint32_t end) {
    if (start <= end) {
        for (uint32_t brightness = start; brightness <= end; brightness++) {
            for (uint32_t frame = 0; frame < 10u; frame++) {
                pwm_frame(brightness);
            }
        }
    } else {
        for (uint32_t brightness = start; brightness > end; brightness--) {
            for (uint32_t frame = 0; frame < 10u; frame++) {
                pwm_frame(brightness);
            }
        }
        for (uint32_t frame = 0; frame < 10u; frame++) {
            pwm_frame(end);
        }
    }
}

int main(void) {
    RCC_AHBENR |= RCC_AHBENR_GPIOAEN;

    GPIOA_MODER &= ~(3u << (LED_PIN * 2u));
    GPIOA_MODER |=  (1u << (LED_PIN * 2u));

    while (1) {
        fade_to(0u, FADE_STEPS - 1u);
        fade_to(FADE_STEPS - 1u, 0u);
    }
}
