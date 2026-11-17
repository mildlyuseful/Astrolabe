# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

if(CONFIG_ASTROLABE)
  target_include_directories(app PRIVATE include)
  target_sources(app PRIVATE src/route.c src/mouse.c src/behaviors.c)
  target_sources_ifdef(CONFIG_ASTROLABE_BLE app PRIVATE src/ble.c)
  target_sources_ifdef(CONFIG_ASTROLABE_USB app PRIVATE src/usb.c)
  target_sources_ifdef(CONFIG_ASTROLABE_INDICATOR app PRIVATE src/indicator.c)
  target_sources_ifdef(CONFIG_ASTROLABE_DUAL_PMW3610 app PRIVATE
    drivers/dual_pmw3610/dual_pmw3610.c
    drivers/dual_pmw3610/fusion.c
  )
endif()
