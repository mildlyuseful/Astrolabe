"""Static compatibility contracts for firmware publishers."""

from pathlib import Path


XIAO = Path("firmware/XIAO3389/XIAO3389.ino").read_text(encoding="utf-8")
PMW3610 = Path("firmware/PMW3610/PMW3610.ino").read_text(encoding="utf-8")
PLACEHOLDER = Path("firmware/Astrolabe/Astrolabe.ino").read_text(encoding="utf-8")


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


def test_xiao_test_bench_debounce_and_button_mapping_are_preserved():
    assert "#define DEBOUNCE_MS      8" in XIAO
    assert "static const uint8_t pins[3]={PIN_BTN_LEFT,PIN_BTN_RIGHT,PIN_BTN_MIDDLE}" in XIAO
    assert "static const uint8_t masks[3]={0x01,0x02,0x04}" in XIAO
    assert "(nowMs-tChange[i])>=DEBOUNCE_MS" in XIAO


def test_pmw3610_fiveway_contract_matches_astrolabe_descriptor():
    assert '#define BLE_NAME        "Astrolabe"' in PMW3610
    assert "#define DEBOUNCE_MS     8" in PMW3610
    assert "#define BALL_DIAMETER_MM   50.8f" in PMW3610
    assert "#define SENSOR_L_PHI    140.0f" in PMW3610
    assert "#define SENSOR_L_THETA  130.0f" in PMW3610
    assert "#define SENSOR_R_PHI    230.0f" in PMW3610
    assert "#define SENSOR_R_THETA  130.0f" in PMW3610
    assert "#define BTN_BIT_UP      0x01" in PMW3610
    assert "#define BTN_BIT_DOWN    0x02" in PMW3610
    assert "#define BTN_BIT_LEFT    0x04" in PMW3610
    assert "#define BTN_BIT_RIGHT   0x08" in PMW3610
    assert "#define BTN_BIT_CENTER  0x10" in PMW3610
    assert "if(protocol & BTN_BIT_DOWN)   hidOut |= 0x01;" in PMW3610
    assert "if(protocol & BTN_BIT_RIGHT)  hidOut |= 0x02;" in PMW3610
    assert "if(protocol & BTN_BIT_CENTER) hidOut |= 0x04;" in PMW3610
    assert "CALIB_SERIAL" in PMW3610
    assert 'Serial.print("CALIB,")' in PMW3610
    assert "POLL_INTERVAL_US 1000" in PMW3610
    assert "MOTION_IDLE_MS" in PMW3610
    assert "trySleepUntilInterrupt" in PMW3610
    assert "sensorsEnterRest" in PMW3610
    assert "clearMotionAccumulators" in PMW3610
    assert "if(g_okL) a = sensorL.readMotion();" in PMW3610
    assert "if(g_okR) b = sensorR.readMotion();" in PMW3610
    assert "T_SRAD_US" in PMW3610
    assert "sdioRelease" in PMW3610
    assert "T_SCLK_NCS_WR_US" in PMW3610
    # PMW3610 DELTA_XY_H: upper nibble = X[11:8], lower nibble = Y[11:8]
    assert "((xy_h & 0xF0) << 4) | x_l" in PMW3610
    assert "((xy_h & 0x0F) << 8) | y_l" in PMW3610
    assert "((xy_h & 0x0F) << 8) | x_l" not in PMW3610
    assert "HID_DRAIN_MAX" in PMW3610
    # Failed-notify retention while a controller is subscribed: the success branch and
    # the !g_controller fallback both clear the accumulators, and nothing else may.
    assert "if(rotationChar.notify(rbuf, sizeof(rbuf)))" in PMW3610
    assert "} else if(!g_controller){" in PMW3610
    assert "P0_20" in PMW3610 or "D3" in PMW3610
    assert "P1_00" in PMW3610 or "D6" in PMW3610
    assert "warnBlink" in PMW3610
    assert "BLE advertising as Astrolabe" in PMW3610
    assert "PIN_017" not in PMW3610
    assert "attachInterrupt(digitalPinToInterrupt(PIN_MOTION_L)" in PMW3610
    assert "attachInterrupt(digitalPinToInterrupt(PIN_MOTION_R)" in PMW3610
    assert "waitForEvent();" in PMW3610
    assert "IPS_CAP" not in PMW3610
    assert "clampSensorIps" not in PMW3610


def test_production_placeholder_records_version_without_fabricating_hardware():
    assert "#define ASTROLABE_INPUT_PROTOCOL_VERSION 1" in PLACEHOLDER
    assert "#define ASTROLABE_FIRMWARE_PROTOCOL_REV   1" in PLACEHOLDER
    assert "active-low inputs with internal pull-ups and firmware debounce" in PLACEHOLDER
    assert "PMW3610/PMW3610.ino" in PLACEHOLDER
    assert "publish the full" in PLACEHOLDER
    assert "observed bitset if simultaneous inputs occur" in PLACEHOLDER
