# SPDX-FileCopyrightText: 2026 Dylan Lee
# SPDX-License-Identifier: Apache-2.0

"""Static compatibility contracts for firmware publishers.

Scope is the BLE/daemon protocol only: service and characteristic UUIDs, the fixed packet
layouts, sequencing, and controller-vs-HID ownership. Hardware choices — pin maps, sensor
poses, ball diameter, board part numbers, debounce and poll timing, register bit layouts —
are deliberately not asserted here. The hardware is still being revised, and pinning those
literals only produced test failures that tracked bench iteration rather than a real
regression.
"""

from pathlib import Path


XIAO = Path("firmware/XIAO3389/XIAO3389.ino").read_text(encoding="utf-8")
PMW3610 = Path("firmware/PMW3610/PMW3610.ino").read_text(encoding="utf-8")


def test_rotation_service_and_fixed_packet_contract_remain_unchanged():
    for firmware in (XIAO, PMW3610):
        assert "0x01,0x00,0xAD,0x2C" in firmware
        assert "0x02,0x00,0xAD,0x2C" in firmware
        assert "rotationChar.setFixedLen(12)" in firmware
        assert "float rbuf[3]" in firmware
        assert "rotationChar.notify(rbuf, sizeof(rbuf))" in firmware


def test_additive_input_characteristic_publishes_protocol_v1_full_snapshots():
    for firmware in (XIAO, PMW3610):
        assert "0x03,0x00,0xAD,0x2C" in firmware
        assert "#define ASTROLABE_INPUT_PROTOCOL_VERSION  1" in firmware
        assert "#define ASTROLABE_FIRMWARE_PROTOCOL_REV    1" in firmware
        assert "#define ASTROLABE_INPUT_PACKET_BYTES      6" in firmware
        assert "inputStateChar.setFixedLen(ASTROLABE_INPUT_PACKET_BYTES)" in firmware
        assert "ASTROLABE_INPUT_PROTOCOL_VERSION," in firmware
        assert "ASTROLABE_INPUT_KIND_STATE," in firmware
        assert "(uint8_t)(g_inputSequence & 0xFF)" in firmware
        assert "(uint8_t)(g_inputSequence >> 8)" in firmware
        assert "ASTROLABE_INPUT_STATE_BYTES," in firmware
        assert "g_protocolButtons" in firmware


def test_state_changes_increment_sequence_and_subscription_sends_initial_snapshot():
    for firmware in (XIAO, PMW3610):
        assert "g_inputSequence++;" in firmware
        assert "notifyInputState();" in firmware
        assert "if(chr->notifyEnabled(conn_hdl)) notifyInputState(conn_hdl);" in firmware
    assert "if(g_buttons != g_protocolButtons)" in XIAO
    assert "if(protocolButtons != g_protocolButtons)" in PMW3610


def test_controller_mode_and_daemon_absent_hid_fallback_remain_rotation_owned():
    for firmware in (XIAO, PMW3610):
        assert "rotationChar.setCccdWriteCallback(rotCccdCallback)" in firmware
        assert "inputStateChar.setCccdWriteCallback(inputCccdCallback)" in firmware
        assert "if(chr->notifyEnabled(conn_hdl)){ g_controller = true;" in firmware
        assert "if(!g_controller){" in firmware
        assert "blehid.mouseButtonRelease()" in firmware
    assert "blehid.mouseButtonPress(g_buttons)" in XIAO
    assert "blehid.mouseButtonPress(g_hidButtons)" in PMW3610
