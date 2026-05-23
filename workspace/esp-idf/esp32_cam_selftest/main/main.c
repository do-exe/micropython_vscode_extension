#include "esp_camera.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "esp32_cam_selftest";

#define CAM_PIN_PWDN 32
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 0
#define CAM_PIN_SIOD 26
#define CAM_PIN_SIOC 27

#define CAM_PIN_D7 35
#define CAM_PIN_D6 34
#define CAM_PIN_D5 39
#define CAM_PIN_D4 36
#define CAM_PIN_D3 21
#define CAM_PIN_D2 19
#define CAM_PIN_D1 18
#define CAM_PIN_D0 5
#define CAM_PIN_VSYNC 25
#define CAM_PIN_HREF 23
#define CAM_PIN_PCLK 22

static camera_config_t camera_config(void) {
    camera_config_t config = {
        .pin_pwdn = CAM_PIN_PWDN,
        .pin_reset = CAM_PIN_RESET,
        .pin_xclk = CAM_PIN_XCLK,
        .pin_sccb_sda = CAM_PIN_SIOD,
        .pin_sccb_scl = CAM_PIN_SIOC,
        .pin_d7 = CAM_PIN_D7,
        .pin_d6 = CAM_PIN_D6,
        .pin_d5 = CAM_PIN_D5,
        .pin_d4 = CAM_PIN_D4,
        .pin_d3 = CAM_PIN_D3,
        .pin_d2 = CAM_PIN_D2,
        .pin_d1 = CAM_PIN_D1,
        .pin_d0 = CAM_PIN_D0,
        .pin_vsync = CAM_PIN_VSYNC,
        .pin_href = CAM_PIN_HREF,
        .pin_pclk = CAM_PIN_PCLK,
        .xclk_freq_hz = 10000000,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_RGB565,
        .frame_size = FRAMESIZE_QQVGA,
        .jpeg_quality = 12,
        .fb_count = 1,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
        .fb_location = CAMERA_FB_IN_PSRAM,
    };
    return config;
}

void app_main(void) {
    ESP_LOGI(TAG, "ESP32-CAM self-test starting");

    camera_config_t config = camera_config();
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: 0x%x", err);
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }

    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor != NULL) {
        ESP_LOGI(TAG, "Camera sensor PID=0x%02x VER=0x%02x", sensor->id.PID, sensor->id.VER);
        sensor->set_framesize(sensor, FRAMESIZE_QQVGA);
    }

    uint32_t frame_count = 0;
    while (1) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGE(TAG, "Capture failed");
        } else {
            frame_count++;
            ESP_LOGI(
                TAG,
                "Frame %lu captured: %ux%u, %u bytes, format=%u",
                (unsigned long)frame_count,
                frame->width,
                frame->height,
                frame->len,
                frame->format
            );
            esp_camera_fb_return(frame);
        }
        vTaskDelay(pdMS_TO_TICKS(3000));
    }
}
