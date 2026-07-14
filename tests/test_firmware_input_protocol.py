"""Static compatibility contracts for the working XIAO3389 firmware publisher."""

from pathlib import Path


FIRMWARE = Path("firmware/XIAO3389/XIAO3389.ino").read_text(encoding="utf-8")
PLACEHOLDER = Path("firmware/Astrolabe/Astrolabe.ino").read_text(encoding="utf-8")


def test_rotation_service_and_fixed_packet_contract_remain_unchanged():
    assert "0x01,0x00,0xAD,0x2C" in FIRMWARE
    assert "0x02,0x00,0xAD,0x2C" in FIRMWARE
    assert "rotationChar.setFixedLen(12)" in FIRMWARE
    assert "float rbuf[3]" in FIRMWARE
    assert "rotationChar.notify(rbuf, sizeof(rbuf))" in FIRMWARE


def test_additive_input_characteristic_publishes_protocol_v1_full_snapshots():
    assert "0x03,0x00,0xAD,0x2C" in FIRMWARE
    assert "#define ASTROLABE_INPUT_PROTOCOL_VERSION  1" in FIRMWARE
    assert "#define ASTROLABE_FIRMWARE_PROTOCOL_REV    1" in FIRMWARE
    assert "#define ASTROLABE_INPUT_PACKET_BYTES      6" in FIRMWARE
    assert "inputStateChar.setFixedLen(ASTROLABE_INPUT_PACKET_BYTES)" in FIRMWARE
    assert "ASTROLABE_INPUT_PROTOCOL_VERSION," in FIRMWARE
    assert "ASTROLABE_INPUT_KIND_STATE," in FIRMWARE
    assert "(uint8_t)(g_inputSequence & 0xFF)" in FIRMWARE
    assert "(uint8_t)(g_inputSequence >> 8)" in FIRMWARE
    assert "ASTROLABE_INPUT_STATE_BYTES," in FIRMWARE
    assert "g_protocolButtons" in FIRMWARE


def test_state_changes_increment_sequence_and_subscription_sends_initial_snapshot():
    assert "if(g_buttons != g_protocolButtons)" in FIRMWARE
    assert "g_inputSequence++;" in FIRMWARE
    assert "notifyInputState();" in FIRMWARE
    assert "if(chr->notifyEnabled(conn_hdl)) notifyInputState(conn_hdl);" in FIRMWARE


def test_controller_mode_and_daemon_absent_hid_fallback_remain_rotation_owned():
    assert "rotationChar.setCccdWriteCallback(rotCccdCallback)" in FIRMWARE
    assert "inputStateChar.setCccdWriteCallback(inputCccdCallback)" in FIRMWARE
    assert "if(chr->notifyEnabled(conn_hdl)){ g_controller = true;" in FIRMWARE
    assert "if(!g_controller){" in FIRMWARE
    assert "blehid.mouseButtonPress(g_buttons)" in FIRMWARE
    assert "blehid.mouseButtonRelease()" in FIRMWARE


def test_test_bench_debounce_and_button_mapping_are_preserved():
    assert "#define DEBOUNCE_MS      8" in FIRMWARE
    assert "static const uint8_t pins[3]={PIN_BTN_LEFT,PIN_BTN_RIGHT,PIN_BTN_MIDDLE}" in FIRMWARE
    assert "static const uint8_t masks[3]={0x01,0x02,0x04}" in FIRMWARE
    assert "(nowMs-tChange[i])>=DEBOUNCE_MS" in FIRMWARE


def test_production_placeholder_records_version_without_fabricating_hardware():
    assert "#define ASTROLABE_INPUT_PROTOCOL_VERSION 1" in PLACEHOLDER
    assert "#define ASTROLABE_FIRMWARE_PROTOCOL_REV   1" in PLACEHOLDER
    assert "does not invent pins, polarity, or debounce" in PLACEHOLDER
