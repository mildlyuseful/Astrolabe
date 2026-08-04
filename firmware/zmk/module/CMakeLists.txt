# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

if(CONFIG_ASTROLABE_FIRMWARE)
  target_include_directories(app PRIVATE include)
  target_sources(app PRIVATE
    drivers/pmw3610/pmw3610.c
    src/behavior_control.c
    src/fusion.c
    src/protocol.c
    src/route.c
  )
  target_sources_ifdef(CONFIG_ASTROLABE_GATT app PRIVATE src/gatt.c)
  target_sources_ifdef(CONFIG_ASTROLABE_USB app PRIVATE src/usb.c)
  target_sources_ifdef(CONFIG_ASTROLABE_LED_PIN_TEST app PRIVATE src/led_pin_test.c)
endif()
