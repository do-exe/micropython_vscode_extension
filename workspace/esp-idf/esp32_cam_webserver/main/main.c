#include <string.h>
#include <stdlib.h>
#include <stdio.h>

#include "esp_camera.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "img_converters.h"
#include "nvs_flash.h"

static const char *TAG = "esp32_cam_webserver";

#define WIFI_AP_SSID "ESP32-CAM"
#define WIFI_AP_PASSWORD "12345678"
#define WIFI_AP_CHANNEL 1
#define WIFI_AP_MAX_CLIENTS 2
#define CAPTURE_JPEG_QUALITY 90
#define STREAM_JPEG_QUALITY 68
#define STREAM_FRAME_DELAY_MS 15

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
        .pixel_format = PIXFORMAT_YUV422,
        .frame_size = FRAMESIZE_QVGA,
        .jpeg_quality = 12,
        .fb_count = 1,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
        .fb_location = CAMERA_FB_IN_PSRAM,
    };
    return config;
}

static esp_err_t index_handler(httpd_req_t *req) {
    static const char html[] =
        "<!doctype html><html><head>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>ESP32-CAM</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;background:#f5f5f5;color:#111}"
        "img{max-width:100%;border:1px solid #aaa;background:#fff}"
        "button,a{display:inline-block;margin:4px 8px 4px 0;font-size:16px}"
        "label{display:block;margin:10px 0}.row{margin:12px 0}</style>"
        "<script>"
        "function setv(k,v){fetch('/control?var='+k+'&val='+v).then(()=>setTimeout(()=>{document.getElementById('view').src='/stream?t='+Date.now()},120))}"
        "function preset(n){fetch('/preset?name='+n).then(()=>setTimeout(()=>{document.getElementById('view').src='/stream?t='+Date.now()},120))}"
        "</script>"
        "</head><body>"
        "<h1>ESP32-CAM</h1>"
        "<p><a href=\"/capture\">Capture JPEG</a><a href=\"/stream\">Live Stream</a></p>"
        "<div class=\"row\">"
        "<button onclick=\"preset('natural')\">Natural</button>"
        "<button onclick=\"preset('bright')\">Bright</button>"
        "<button onclick=\"preset('lowlight')\">Low light</button>"
        "<button onclick=\"preset('cool')\">Cool</button>"
        "<button onclick=\"preset('warm')\">Warm</button>"
        "<button onclick=\"preset('repair')\">Repair</button>"
        "</div>"
        "<label>Brightness <input type=\"range\" min=\"-2\" max=\"2\" value=\"0\" onchange=\"setv('brightness',this.value)\"></label>"
        "<label>Contrast <input type=\"range\" min=\"-2\" max=\"2\" value=\"1\" onchange=\"setv('contrast',this.value)\"></label>"
        "<label>Saturation <input type=\"range\" min=\"-2\" max=\"2\" value=\"0\" onchange=\"setv('saturation',this.value)\"></label>"
        "<label>AE level <input type=\"range\" min=\"-2\" max=\"2\" value=\"0\" onchange=\"setv('ae',this.value)\"></label>"
        "<label>WB mode <input type=\"range\" min=\"0\" max=\"4\" value=\"0\" onchange=\"setv('wb',this.value)\"></label>"
        "<label><input type=\"checkbox\" onchange=\"setv('hmirror',this.checked?1:0)\"> Mirror</label>"
        "<label><input type=\"checkbox\" onchange=\"setv('vflip',this.checked?1:0)\"> Flip</label>"
        "<img id=\"view\" src=\"/stream\">"
        "</body></html>";

    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, html, HTTPD_RESP_USE_STRLEN);
}

static void apply_sensor_preset(const char *name) {
    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == NULL) {
        return;
    }

    sensor->set_whitebal(sensor, 1);
    sensor->set_awb_gain(sensor, 1);
    sensor->set_exposure_ctrl(sensor, 1);
    sensor->set_aec2(sensor, 1);
    sensor->set_gain_ctrl(sensor, 1);
    sensor->set_bpc(sensor, 1);
    sensor->set_wpc(sensor, 1);
    sensor->set_lenc(sensor, 1);
    sensor->set_raw_gma(sensor, 1);
    sensor->set_special_effect(sensor, 0);
    sensor->set_dcw(sensor, 1);

    if (strcmp(name, "bright") == 0) {
        sensor->set_brightness(sensor, 1);
        sensor->set_contrast(sensor, 1);
        sensor->set_saturation(sensor, 1);
        sensor->set_ae_level(sensor, 1);
        sensor->set_wb_mode(sensor, 0);
    } else if (strcmp(name, "lowlight") == 0) {
        sensor->set_brightness(sensor, 2);
        sensor->set_contrast(sensor, 0);
        sensor->set_saturation(sensor, 0);
        sensor->set_ae_level(sensor, 2);
        sensor->set_wb_mode(sensor, 0);
    } else if (strcmp(name, "cool") == 0) {
        sensor->set_brightness(sensor, 0);
        sensor->set_contrast(sensor, 1);
        sensor->set_saturation(sensor, 0);
        sensor->set_ae_level(sensor, 0);
        sensor->set_wb_mode(sensor, 2);
    } else if (strcmp(name, "warm") == 0) {
        sensor->set_brightness(sensor, 0);
        sensor->set_contrast(sensor, 1);
        sensor->set_saturation(sensor, 0);
        sensor->set_ae_level(sensor, 0);
        sensor->set_wb_mode(sensor, 1);
    } else if (strcmp(name, "repair") == 0) {
        sensor->set_brightness(sensor, 1);
        sensor->set_contrast(sensor, 2);
        sensor->set_saturation(sensor, -1);
        sensor->set_ae_level(sensor, 1);
        sensor->set_wb_mode(sensor, 0);
        sensor->set_denoise(sensor, 2);
        sensor->set_sharpness(sensor, 1);
    } else {
        sensor->set_brightness(sensor, 0);
        sensor->set_contrast(sensor, 1);
        sensor->set_saturation(sensor, 0);
        sensor->set_ae_level(sensor, 0);
        sensor->set_wb_mode(sensor, 0);
        sensor->set_denoise(sensor, 1);
        sensor->set_sharpness(sensor, 0);
    }
}

static esp_err_t control_handler(httpd_req_t *req) {
    char query[96];
    char var[24];
    char val[16];

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(query, "var", var, sizeof(var)) != ESP_OK ||
        httpd_query_key_value(query, "val", val, sizeof(val)) != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing var or val");
        return ESP_FAIL;
    }

    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == NULL) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    int value = atoi(val);
    if (strcmp(var, "brightness") == 0) {
        sensor->set_brightness(sensor, value);
    } else if (strcmp(var, "contrast") == 0) {
        sensor->set_contrast(sensor, value);
    } else if (strcmp(var, "saturation") == 0) {
        sensor->set_saturation(sensor, value);
    } else if (strcmp(var, "ae") == 0) {
        sensor->set_ae_level(sensor, value);
    } else if (strcmp(var, "wb") == 0) {
        sensor->set_wb_mode(sensor, value);
    } else if (strcmp(var, "hmirror") == 0) {
        sensor->set_hmirror(sensor, value);
    } else if (strcmp(var, "vflip") == 0) {
        sensor->set_vflip(sensor, value);
    } else {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "unknown var");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Control set: %s=%d", var, value);
    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, "ok");
}

static esp_err_t preset_handler(httpd_req_t *req) {
    char query[64];
    char name[24] = "natural";

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK) {
        httpd_query_key_value(query, "name", name, sizeof(name));
    }

    apply_sensor_preset(name);
    ESP_LOGI(TAG, "Preset applied: %s", name);
    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, "ok");
}

static esp_err_t capture_handler(httpd_req_t *req) {
    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == NULL) {
        ESP_LOGE(TAG, "Capture failed");
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    uint8_t *jpeg = NULL;
    size_t jpeg_len = 0;
    if (!frame2jpg(frame, CAPTURE_JPEG_QUALITY, &jpeg, &jpeg_len)) {
        ESP_LOGE(TAG, "JPEG conversion failed");
        esp_camera_fb_return(frame);
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
    esp_err_t err = httpd_resp_send(req, (const char *)jpeg, jpeg_len);
    ESP_LOGI(TAG, "JPEG capture sent: %ux%u, %u bytes", frame->width, frame->height, jpeg_len);
    free(jpeg);
    esp_camera_fb_return(frame);
    return err;
}

static esp_err_t stream_handler(httpd_req_t *req) {
    static const char *boundary = "\r\n--frame\r\n";
    static const char *content_type = "multipart/x-mixed-replace;boundary=frame";
    char header[96];

    httpd_resp_set_type(req, content_type);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

    while (true) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGE(TAG, "Stream capture failed");
            return ESP_FAIL;
        }

        uint8_t *jpeg = NULL;
        size_t jpeg_len = 0;
        if (!frame2jpg(frame, STREAM_JPEG_QUALITY, &jpeg, &jpeg_len)) {
            ESP_LOGE(TAG, "Stream JPEG conversion failed");
            esp_camera_fb_return(frame);
            return ESP_FAIL;
        }

        esp_err_t err = httpd_resp_send_chunk(req, boundary, strlen(boundary));
        if (err == ESP_OK) {
            int header_len = snprintf(
                header,
                sizeof(header),
                "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                jpeg_len
            );
            err = httpd_resp_send_chunk(req, header, header_len);
        }
        if (err == ESP_OK) {
            err = httpd_resp_send_chunk(req, (const char *)jpeg, jpeg_len);
        }

        free(jpeg);
        esp_camera_fb_return(frame);
        if (err != ESP_OK) {
            ESP_LOGI(TAG, "Stream client disconnected");
            return err;
        }

        vTaskDelay(pdMS_TO_TICKS(STREAM_FRAME_DELAY_MS));
    }
}

static void start_camera_server(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.ctrl_port = 32768;
    config.stack_size = 8192;

    httpd_handle_t server = NULL;
    ESP_ERROR_CHECK(httpd_start(&server, &config));

    const httpd_uri_t index_uri = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = index_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t capture_uri = {
        .uri = "/capture",
        .method = HTTP_GET,
        .handler = capture_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t stream_uri = {
        .uri = "/stream",
        .method = HTTP_GET,
        .handler = stream_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t control_uri = {
        .uri = "/control",
        .method = HTTP_GET,
        .handler = control_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t preset_uri = {
        .uri = "/preset",
        .method = HTTP_GET,
        .handler = preset_handler,
        .user_ctx = NULL,
    };

    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &index_uri));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &capture_uri));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &stream_uri));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &control_uri));
    ESP_ERROR_CHECK(httpd_register_uri_handler(server, &preset_uri));
    ESP_LOGI(TAG, "Camera webserver ready: http://192.168.4.1/");
}

static void start_wifi_ap(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_ap();

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));

    wifi_config_t wifi_config = {
        .ap = {
            .ssid = WIFI_AP_SSID,
            .ssid_len = strlen(WIFI_AP_SSID),
            .channel = WIFI_AP_CHANNEL,
            .password = WIFI_AP_PASSWORD,
            .max_connection = WIFI_AP_MAX_CLIENTS,
            .authmode = WIFI_AUTH_WPA_WPA2_PSK,
            .pmf_cfg = {
                .required = false,
            },
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Wi-Fi AP started: ssid=%s password=%s url=http://192.168.4.1/", WIFI_AP_SSID, WIFI_AP_PASSWORD);
}

static void configure_sensor(void) {
    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == NULL) {
        return;
    }

    ESP_LOGI(TAG, "Camera sensor PID=0x%02x VER=0x%02x", sensor->id.PID, sensor->id.VER);
    sensor->set_framesize(sensor, FRAMESIZE_QVGA);
    sensor->set_whitebal(sensor, 1);
    sensor->set_awb_gain(sensor, 1);
    sensor->set_wb_mode(sensor, 0);
    sensor->set_exposure_ctrl(sensor, 1);
    sensor->set_aec2(sensor, 1);
    sensor->set_gain_ctrl(sensor, 1);
    sensor->set_bpc(sensor, 1);
    sensor->set_wpc(sensor, 1);
    sensor->set_lenc(sensor, 1);
    sensor->set_raw_gma(sensor, 1);
    sensor->set_special_effect(sensor, 0);
    sensor->set_dcw(sensor, 1);
    apply_sensor_preset("natural");
}

void app_main(void) {
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_result);

    ESP_LOGI(TAG, "ESP32-CAM webserver starting");

    camera_config_t config = camera_config();
    ESP_ERROR_CHECK(esp_camera_init(&config));

    configure_sensor();

    start_wifi_ap();
    start_camera_server();
}
