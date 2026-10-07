// Mapas de pines de cámara. Verificá contra el esquema de TU placa antes de conectar nada más.
#pragma once
#include "config.h"

#if CAM_BOARD == 1            // Seeed XIAO ESP32S3 Sense
  #define PWDN_GPIO_NUM   -1
  #define RESET_GPIO_NUM  -1
  #define XCLK_GPIO_NUM   10
  #define SIOD_GPIO_NUM   40
  #define SIOC_GPIO_NUM   39
  #define Y9_GPIO_NUM     48
  #define Y8_GPIO_NUM     11
  #define Y7_GPIO_NUM     12
  #define Y6_GPIO_NUM     14
  #define Y5_GPIO_NUM     16
  #define Y4_GPIO_NUM     18
  #define Y3_GPIO_NUM     17
  #define Y2_GPIO_NUM     15
  #define VSYNC_GPIO_NUM  38
  #define HREF_GPIO_NUM   47
  #define PCLK_GPIO_NUM   13
#elif CAM_BOARD == 2          // AI-Thinker ESP32-CAM
  #define PWDN_GPIO_NUM   32
  #define RESET_GPIO_NUM  -1
  #define XCLK_GPIO_NUM   0
  #define SIOD_GPIO_NUM   26
  #define SIOC_GPIO_NUM   27
  #define Y9_GPIO_NUM     35
  #define Y8_GPIO_NUM     34
  #define Y7_GPIO_NUM     39
  #define Y6_GPIO_NUM     36
  #define Y5_GPIO_NUM     21
  #define Y4_GPIO_NUM     19
  #define Y3_GPIO_NUM     18
  #define Y2_GPIO_NUM     5
  #define VSYNC_GPIO_NUM  25
  #define HREF_GPIO_NUM   23
  #define PCLK_GPIO_NUM   22
#else                         // 0 = completá con los pines de tu placa
  #define PWDN_GPIO_NUM   -1
  #define RESET_GPIO_NUM  -1
  #define XCLK_GPIO_NUM   -1
  #define SIOD_GPIO_NUM   -1
  #define SIOC_GPIO_NUM   -1
  #define Y9_GPIO_NUM     -1
  #define Y8_GPIO_NUM     -1
  #define Y7_GPIO_NUM     -1
  #define Y6_GPIO_NUM     -1
  #define Y5_GPIO_NUM     -1
  #define Y4_GPIO_NUM     -1
  #define Y3_GPIO_NUM     -1
  #define Y2_GPIO_NUM     -1
  #define VSYNC_GPIO_NUM  -1
  #define HREF_GPIO_NUM   -1
  #define PCLK_GPIO_NUM   -1
  #error "CAM_BOARD 0: completá los pines en camera_pins.h"
#endif
