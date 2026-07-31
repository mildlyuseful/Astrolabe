# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Offline contracts for the SuperMini validation firmware's battery estimate."""

from pathlib import Path


FIRMWARE = Path("firmware/PMW3610/PMW3610.ino").read_text(encoding="utf-8")


def test_standard_ble_battery_service_is_published() -> None:
    assert "BLEBas blebas;" in FIRMWARE
    assert "blebas.begin();" in FIRMWARE
    assert "blebas.write(g_batteryPercent);" in FIRMWARE
    assert "blebas.notify(next)" in FIRMWARE


def test_battery_uses_internal_vddh_input_at_low_frequency() -> None:
    assert "#define BATTERY_SAMPLE_MS 60000UL" in FIRMWARE
    assert "analogReadVDDHDIV5()" in FIRMWARE
    assert "analogReference(AR_INTERNAL_1_2);" in FIRMWARE
    assert "for(uint8_t i=0; i<8; i++)" in FIRMWARE


def test_usb_voltage_is_not_reported_as_battery_voltage() -> None:
    assert "POWER_USBREGSTATUS_VBUSDETECT_Msk" in FIRMWARE
    assert "bool usbRemoved=g_usbPowerPresent && !usbPresent;" in FIRMWARE
    assert "if(usbPresent) return;" in FIRMWARE
    assert "if(!force && !usbRemoved" in FIRMWARE
