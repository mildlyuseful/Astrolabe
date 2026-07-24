// SPDX-FileCopyrightText: 2026 Dylan Lee
// SPDX-License-Identifier: Apache-2.0

/*
 * Dual PMW3389 Trackball -> BLE HID Mouse  (Seeed XIAO nRF52840, Adafruit Bluefruit)
 * Board package: "Seeed nRF52 Boards" (non-mbed). Adafruit_TinyUSB.h included for Serial.
 * Shared SPI: SCK=D8 MISO=D9 MOSI=D10.  CS_A=D7  CS_B=D6.  Buttons L/R/M = D0/D1/D2 to GND.
 *
 * EXTENSION: in addition to the HID mouse, this also broadcasts fused 3-axis ball
 * rotation over a custom 128-bit GATT service. A second additive notify characteristic
 * publishes versioned full button-state snapshots for daemon/controller mode. The HID
 * mouse is unchanged and fully functional whether or not anything subscribes to the
 * rotation stream. See the blocks tagged "[GATT]" below.
 */
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <SPI.h>
#include <math.h>

#ifndef LED_STATE_ON
#define LED_STATE_ON LOW
#endif

#define PIN_CS_A        D7
#define PIN_CS_B        D6
#define PIN_BTN_LEFT    D0
#define PIN_BTN_RIGHT   D1
#define PIN_BTN_MIDDLE  D2

#define SENSOR_A_PHI   240.0f
#define SENSOR_A_THETA 125.0f
#define SENSOR_B_PHI   120.0f
#define SENSOR_B_THETA 125.0f
#define SENSOR_A_MOUNT_DEG  90.0f
#define SENSOR_A_FLIP       0
#define SENSOR_B_MOUNT_DEG  90.0f
#define SENSOR_B_FLIP       0

#define SENSOR_CPI       1600    // raw/uncapped; pairs with CURSOR_GAIN (3200/0.0625 for full res)
#define CURSOR_GAIN      0.125f
#define CURSOR_SWAP_XY   1
#define CURSOR_INVERT_X  1
#define CURSOR_INVERT_Y  1
#define SCROLL_GAIN          0.25f
#define SCROLL_DIVISOR       60.0f
#define SCROLL_INVERT        1
#define YAW_DEADZONE         1.2f
#define YAW_DOMINANCE_RATIO  1.7f
#define SCROLL_HOLD_MS       80

#define BLE_NAME        "Trackball BLE"
#define BLE_SEND_MS     7
#define POLL_INTERVAL_US 1000    // 1 kHz sensor poll
#define DEBOUNCE_MS      8
#define DEBUG_PRINT      1
#define IPS_REPORT_MS    8000    // how often to print peak raw sensor speed (ips) over Serial
#define IPS_CAP          30.0f   // emulated PMW3610 tracking ceiling (ips); set huge (9999) to disable

// ---------------------------------------------------------------------------
// [GATT] Custom 3-axis rotation service (purely additive; HID mouse untouched)
// ---------------------------------------------------------------------------
// Random 128-bit base UUID; the service uses field 0x0001 and the characteristic
// fields 0x0002/0x0003. These MUST match the daemon exactly:
//   service        2cad0001-6e64-0146-b139-9cf2a4cd57fc
//   rotation       2cad0002-6e64-0146-b139-9cf2a4cd57fc (fixed 12-byte float32 x 3)
//   input state    2cad0003-6e64-0146-b139-9cf2a4cd57fc (protocol v1 snapshot)
// Adafruit Bluefruit takes 128-bit UUIDs little-endian (reverse of the strings above).
const uint8_t ROT_SERVICE_UUID[16] = {
  0xFC,0x57,0xCD,0xA4,0xF2,0x9C,0x39,0xB1,0x46,0x01,0x64,0x6E,0x01,0x00,0xAD,0x2C };
const uint8_t ROT_CHAR_UUID[16]    = {
  0xFC,0x57,0xCD,0xA4,0xF2,0x9C,0x39,0xB1,0x46,0x01,0x64,0x6E,0x02,0x00,0xAD,0x2C };
const uint8_t INPUT_CHAR_UUID[16]  = {
  0xFC,0x57,0xCD,0xA4,0xF2,0x9C,0x39,0xB1,0x46,0x01,0x64,0x6E,0x03,0x00,0xAD,0x2C };

#define ASTROLABE_INPUT_PROTOCOL_VERSION  1
#define ASTROLABE_FIRMWARE_PROTOCOL_REV    1
#define ASTROLABE_INPUT_KIND_STATE        1
#define ASTROLABE_INPUT_STATE_BYTES       1
#define ASTROLABE_INPUT_PACKET_BYTES      6

// 1:1 conversion. The solver outputs wx/wy/wz = R_counts * (true radians per poll),
// where R_counts is the ball radius expressed in sensor counts. Divide by R_counts
// to recover true radians, accumulate per poll, and ship the integrated delta each
// notify (so total cube rotation tracks the ball regardless of packet timing).
#define BALL_DIAMETER_MM   55.0f
#define ROT_SIGN_X         -1.0f   // roll  — flip here if needed (daemon also has knobs)
#define ROT_SIGN_Y         -1.0f   // pitch
#define ROT_SIGN_Z         -1.0f   // yaw
static const float COUNTS_PER_MM = (float)SENSOR_CPI / 25.4f;
static const float R_COUNTS      = (BALL_DIAMETER_MM * 0.5f) * COUNTS_PER_MM; // ~866.1 @ 800 CPI, 55 mm

#define REG_Product_ID          0x00
#define REG_Motion              0x02
#define REG_Delta_Y_H           0x06
#define REG_Resolution_L        0x0E
#define REG_Resolution_H        0x0F
#define REG_Config2             0x10
#define REG_Power_Up_Reset      0x3A
#define REG_Inverse_Product_ID  0x3F
#define REG_Motion_Burst        0x50
#define REG_Lift_Config         0x63
#define PMW3389_PRODUCT_ID      0x47
#define PMW3389_INV_PRODUCT_ID  0xB8

struct PMW3389_DATA { bool isMotion; bool isOnSurface; int16_t dx; int16_t dy; uint8_t SQUAL; };

class PMW3389 {
public:
  bool begin(uint8_t csPin, uint16_t cpi) {
    _cs = csPin; pinMode(_cs, OUTPUT); digitalWrite(_cs, HIGH);
    csLow();  delayMicroseconds(40);
    csHigh(); delayMicroseconds(40);
    writeReg(REG_Power_Up_Reset, 0x5A); delay(50);
    for (uint8_t r = REG_Motion; r <= REG_Delta_Y_H; r++) readReg(r);
    delay(10); setCPI(cpi); delay(1);
    writeReg(REG_Config2, 0x00);
    writeReg(REG_Lift_Config, 0x02);
    return (readReg(REG_Product_ID) == PMW3389_PRODUCT_ID) &&
           (readReg(REG_Inverse_Product_ID) == PMW3389_INV_PRODUCT_ID);
  }
  void setCPI(uint16_t cpi) {
    if (cpi < 50) cpi = 50; if (cpi > 16000) cpi = 16000;
    uint16_t v = cpi / 50 - 1;
    writeReg(REG_Resolution_H, (v >> 8) & 0xFF);
    writeReg(REG_Resolution_L,  v       & 0xFF);
  }
  PMW3389_DATA readBurst() {
    PMW3389_DATA d = {false, false, 0, 0, 0};
    if (!_inBurst) { writeReg(REG_Motion_Burst, 0x00); _inBurst = true; }
    csLow(); SPI.transfer(REG_Motion_Burst); delayMicroseconds(35);
    uint8_t buf[7]; for (uint8_t i = 0; i < 7; i++) buf[i] = SPI.transfer(0x00);
    csHigh();
    if (buf[0] & 0b111) _inBurst = false;
    d.isMotion    = (buf[0] & 0x80) != 0;
    d.isOnSurface = (buf[0] & 0x08) == 0;
    d.dx    = (int16_t)((uint16_t)buf[3] << 8 | buf[2]);
    d.dy    = (int16_t)((uint16_t)buf[5] << 8 | buf[4]);
    d.SQUAL = buf[6];
    return d;
  }
  uint8_t readReg(uint8_t addr) {
    if (addr != REG_Motion_Burst) _inBurst = false;
    csLow(); SPI.transfer(addr & 0x7F); delayMicroseconds(160);
    uint8_t data = SPI.transfer(0x00); delayMicroseconds(1);
    csHigh(); delayMicroseconds(19); return data;
  }
  void writeReg(uint8_t addr, uint8_t data) {
    if (addr != REG_Motion_Burst) _inBurst = false;
    csLow(); SPI.transfer(addr | 0x80); SPI.transfer(data);
    delayMicroseconds(35); csHigh(); delayMicroseconds(145);
  }
private:
  uint8_t _cs = 0xFF; bool _inBurst = false;
  void csLow()  { SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE3));
                  digitalWrite(_cs, LOW); delayMicroseconds(1); }
  void csHigh() { digitalWrite(_cs, HIGH); SPI.endTransaction(); }
};

float A[4][3];
float pinv[3][4];

static void cross3(const float a[3], const float b[3], float o[3]) {
  o[0]=a[1]*b[2]-a[2]*b[1]; o[1]=a[2]*b[0]-a[0]*b[2]; o[2]=a[0]*b[1]-a[1]*b[0];
}
static float norm3(const float a[3]) { return sqrtf(a[0]*a[0]+a[1]*a[1]+a[2]*a[2]); }

static void localFrame(const float n[3], float e_az[3], float e_up[3]) {
  const float Z[3] = {0,0,1};
  cross3(Z, n, e_az);
  float la = norm3(e_az);
  if (la < 1e-6f) { e_az[0]=1; e_az[1]=0; e_az[2]=0; }
  else            { for (int k=0;k<3;k++) e_az[k] /= la; }
  cross3(n, e_az, e_up);
  float lu = norm3(e_up); for (int k=0;k<3;k++) e_up[k] /= lu;
}

static void sensorAxes(const float n[3], float mountDeg, bool flip,
                       float dxDir[3], float dyDir[3]) {
  float e_az[3], e_up[3]; localFrame(n, e_az, e_up);
  float bx[3], by[3];
  for (int k=0;k<3;k++) { bx[k] = -e_az[k]; by[k] = e_up[k]; }
  float a = mountDeg * (float)M_PI / 180.0f, ca = cosf(a), sa = sinf(a);
  float fl = flip ? -1.0f : 1.0f;
  for (int k=0;k<3;k++) {
    dxDir[k] =  ca*bx[k] + sa*by[k];
    dyDir[k] = (-sa*bx[k] + ca*by[k]) * fl;
  }
}

static bool invert3x3(const float m[3][3], float out[3][3]) {
  float det = m[0][0]*(m[1][1]*m[2][2]-m[1][2]*m[2][1])
            - m[0][1]*(m[1][0]*m[2][2]-m[1][2]*m[2][0])
            + m[0][2]*(m[1][0]*m[2][1]-m[1][1]*m[2][0]);
  if (fabsf(det) < 1e-9f) return false;
  float iv = 1.0f/det;
  out[0][0]= (m[1][1]*m[2][2]-m[1][2]*m[2][1])*iv;
  out[0][1]=-(m[0][1]*m[2][2]-m[0][2]*m[2][1])*iv;
  out[0][2]= (m[0][1]*m[1][2]-m[0][2]*m[1][1])*iv;
  out[1][0]=-(m[1][0]*m[2][2]-m[1][2]*m[2][0])*iv;
  out[1][1]= (m[0][0]*m[2][2]-m[0][2]*m[2][0])*iv;
  out[1][2]=-(m[0][0]*m[1][2]-m[0][2]*m[1][0])*iv;
  out[2][0]= (m[1][0]*m[2][1]-m[1][1]*m[2][0])*iv;
  out[2][1]=-(m[0][0]*m[2][1]-m[0][1]*m[2][0])*iv;
  out[2][2]= (m[0][0]*m[1][1]-m[0][1]*m[1][0])*iv;
  return true;
}

static bool buildSolver() {
  const float D2R = (float)M_PI/180.0f;
  float nA[3] = { sinf(SENSOR_A_THETA*D2R)*cosf(SENSOR_A_PHI*D2R),
                  sinf(SENSOR_A_THETA*D2R)*sinf(SENSOR_A_PHI*D2R),
                  cosf(SENSOR_A_THETA*D2R) };
  float nB[3] = { sinf(SENSOR_B_THETA*D2R)*cosf(SENSOR_B_PHI*D2R),
                  sinf(SENSOR_B_THETA*D2R)*sinf(SENSOR_B_PHI*D2R),
                  cosf(SENSOR_B_THETA*D2R) };
  float aDx[3],aDy[3],bDx[3],bDy[3];
  sensorAxes(nA, SENSOR_A_MOUNT_DEG, SENSOR_A_FLIP, aDx, aDy);
  sensorAxes(nB, SENSOR_B_MOUNT_DEG, SENSOR_B_FLIP, bDx, bDy);
  cross3(nA, aDx, A[0]); cross3(nA, aDy, A[1]);
  cross3(nB, bDx, A[2]); cross3(nB, bDy, A[3]);
  float AtA[3][3] = {{0}};
  for (int i=0;i<3;i++) for (int j=0;j<3;j++)
    for (int r=0;r<4;r++) AtA[i][j] += A[r][i]*A[r][j];
  float AtAinv[3][3];
  if (!invert3x3(AtA, AtAinv)) return false;
  for (int i=0;i<3;i++) for (int c=0;c<4;c++) {
    float s=0; for (int j=0;j<3;j++) s += AtAinv[i][j]*A[c][j];
    pinv[i][c]=s;
  }
  return true;
}

BLEDis bledis;
BLEHidAdafruit blehid;
// [GATT] compatibility-frozen rotation plus additive input-state notification
BLEService        rotationService(ROT_SERVICE_UUID);
BLECharacteristic rotationChar(ROT_CHAR_UUID);
BLECharacteristic inputStateChar(INPUT_CHAR_UUID);
PMW3389 sensorA, sensorB;

float    accX=0, accY=0, accScroll=0;
// [GATT] integrated ball rotation since the last notify, in radians (float -> no remainder carry)
float    gx=0, gy=0, gz=0;
uint8_t  lastButtons=0, g_buttons=0;
uint8_t  g_protocolButtons=0;
uint16_t g_inputSequence=0;
uint32_t lastPollUs=0, lastScrollMs=0, lastSendMs=0, lastBlinkMs=0;
bool     ledState=false;

// [MODE] Seamless software toggle between "HID mouse" and "3-axis controller".
// A daemon subscribing to the rotation stream (CCCD notify-enable) flips us into
// controller mode: we stop driving the HID mouse and only stream rotation. The daemon
// unsubscribing -- or disconnecting -- returns us to plain mouse mode. No button needed.
volatile bool g_controller = false;
uint16_t      g_ctrlConn   = BLE_CONN_HANDLE_INVALID;   // which link owns controller mode

static int8_t clamp8(float v){ return v>127?127:(v<-127?-127:(int8_t)v); }

// [IPS] peak raw sensor speed (inches/sec). *Peak* resets each Serial report; *Max* is
// the all-time session high = the minimum tracking speed this device actually demands.
float    gPeakIpsA=0, gPeakIpsB=0, gMaxIpsA=0, gMaxIpsB=0;
uint32_t lastIpsReportMs=0;

// raw counts/poll -> inches/sec, given the actual elapsed time for this poll
static inline float countsToIps(int16_t dx, int16_t dy, float dtSec){
  if(dtSec <= 0.0f) return 0.0f;
  return sqrtf((float)dx*dx + (float)dy*dy) / (float)SENSOR_CPI / dtSec;
}

// [PMW3610] Cap this poll's raw motion so its speed never exceeds IPS_CAP, using the SAME
// actual-elapsed-time ips formula as countsToIps() -- so "cap at X ips" really means X ips
// as measured, independent of loop period/jitter (the old nominal-1ms cap bit too early).
static void clampSensorIps(int16_t &dx, int16_t &dy, float dtSec){
  float ips = countsToIps(dx, dy, dtSec);
  if(ips > IPS_CAP && ips > 0.0f){
    float s = IPS_CAP / ips;
    dx = (int16_t)lroundf((float)dx * s);
    dy = (int16_t)lroundf((float)dy * s);
  }
}

static void onConnect(uint16_t conn_handle){
  BLEConnection* c = Bluefruit.Connection(conn_handle);
  c->requestConnectionParameter(9);   // 9*1.25 = 11.25 ms
  gx = gy = gz = 0.0f;                 // [GATT] drop any pre-connection accumulation so first packet isn't a jump
#if DEBUG_PRINT
  delay(50);
  uint16_t iv = c->getConnectionInterval();
  Serial.print("conn interval = "); Serial.print(iv*1.25f,2);
  Serial.print(" ms (~"); Serial.print(1000.0f/(iv*1.25f),0); Serial.println(" Hz)");
#endif
}

// [MODE] If the controller link drops, fall back to mouse mode immediately.
static void onDisconnect(uint16_t conn_handle, uint8_t reason){
  (void)reason;
  if(conn_handle == g_ctrlConn){ g_controller = false; g_ctrlConn = BLE_CONN_HANDLE_INVALID; }
}

// [MODE] Fires when a client enables/disables notifications on the rotation characteristic.
// Notify-enabled => a daemon is consuming rotation => controller mode (HID mouse suppressed).
static void rotCccdCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint16_t value){
  (void)value;
  if(chr->notifyEnabled(conn_hdl)){ g_controller = true;  g_ctrlConn = conn_hdl; }
  else if(conn_hdl == g_ctrlConn) { g_controller = false; g_ctrlConn = BLE_CONN_HANDLE_INVALID; }
}

// [INPUT v1] Full-state snapshots repair missed notifications. This test-bench descriptor maps
// bit 0/1/2 to Left/Right/Middle. The production five-way descriptor uses the same packet format
// with bits 0..4, but its physical pin mapping does not belong in this test-bench sketch.
static bool notifyInputState(uint16_t conn_hdl = BLE_CONN_HANDLE_INVALID){
  uint8_t packet[ASTROLABE_INPUT_PACKET_BYTES] = {
    ASTROLABE_INPUT_PROTOCOL_VERSION,
    ASTROLABE_INPUT_KIND_STATE,
    (uint8_t)(g_inputSequence & 0xFF),
    (uint8_t)(g_inputSequence >> 8),
    ASTROLABE_INPUT_STATE_BYTES,
    g_protocolButtons
  };
  if(conn_hdl == BLE_CONN_HANDLE_INVALID) return inputStateChar.notify(packet, sizeof(packet));
  return inputStateChar.notify(conn_hdl, packet, sizeof(packet));
}

// A new subscription/session receives an unconditional baseline before later state changes.
static void inputCccdCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint16_t value){
  (void)value;
  if(chr->notifyEnabled(conn_hdl)) notifyInputState(conn_hdl);
}

static void haltBlink(uint8_t blinks, const char *msg){
  pinMode(LED_BUILTIN, OUTPUT);
  for(;;){
#if DEBUG_PRINT
    Serial.println(msg);
#endif
    for(uint8_t i=0;i<blinks;i++){ digitalWrite(LED_BUILTIN,LED_STATE_ON);delay(150);
      digitalWrite(LED_BUILTIN,!LED_STATE_ON);delay(200);} delay(1200);
  }
}

static uint8_t readButtons(){
  static const uint8_t pins[3]={PIN_BTN_LEFT,PIN_BTN_RIGHT,PIN_BTN_MIDDLE};
  static const uint8_t masks[3]={0x01,0x02,0x04};
  static bool stable[3]={0},lastRaw[3]={0}; static uint32_t tChange[3]={0};
  uint32_t nowMs=millis(); uint8_t out=0;
  for(uint8_t i=0;i<3;i++){
    bool raw=(digitalRead(pins[i])==LOW);
    if(raw!=lastRaw[i]){lastRaw[i]=raw;tChange[i]=nowMs;}
    if(raw!=stable[i] && (nowMs-tChange[i])>=DEBOUNCE_MS) stable[i]=raw;
    if(stable[i]) out|=masks[i];
  }
  return out;
}

// [GATT] register the custom service + notify characteristic. Must run before startAdv().
static void setupRotationService(){
  rotationService.begin();                              // begin the service first...
  rotationChar.setProperties(CHR_PROPS_NOTIFY);         // ...then its characteristic attaches to it
  rotationChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  rotationChar.setFixedLen(12);                         // 3 x float32 (rx,ry,rz) = one BLE packet
  rotationChar.setCccdWriteCallback(rotCccdCallback);   // [MODE] subscribe/unsubscribe == mode switch
  rotationChar.begin();
  inputStateChar.setProperties(CHR_PROPS_NOTIFY);       // additive, never changes controller ownership
  inputStateChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  inputStateChar.setFixedLen(ASTROLABE_INPUT_PACKET_BYTES);
  inputStateChar.setCccdWriteCallback(inputCccdCallback);
  inputStateChar.begin();
}

static void startAdv(){
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addAppearance(BLE_APPEARANCE_HID_MOUSE);
  Bluefruit.Advertising.addService(blehid);
  Bluefruit.Advertising.addName();
  // [GATT] The main advertising packet is already nearly full (flags/txpower/appearance/
  // HID UUID/name), so put the 128-bit rotation service UUID in the scan response. It is
  // still advertised alongside HID; the daemon also discovers it over GATT after connect.
  Bluefruit.ScanResponse.addService(rotationService);
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);
}

void setup(){
  pinMode(PIN_CS_A, OUTPUT); digitalWrite(PIN_CS_A, HIGH);
  pinMode(PIN_CS_B, OUTPUT); digitalWrite(PIN_CS_B, HIGH);
  pinMode(PIN_BTN_LEFT,INPUT_PULLUP); pinMode(PIN_BTN_RIGHT,INPUT_PULLUP);
  pinMode(PIN_BTN_MIDDLE,INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, !LED_STATE_ON);

#if DEBUG_PRINT
  Serial.begin(115200);
  for(uint32_t t0=millis(); !Serial && (millis()-t0)<2000; ) delay(10);
#endif

  bool solverOK = buildSolver();
  SPI.begin(); delay(300);   // XIAO default SPI = SCK D8, MISO D9, MOSI D10

  bool okA = sensorA.begin(PIN_CS_A, SENSOR_CPI);
  if(!okA){ delay(100); okA = sensorA.begin(PIN_CS_A, SENSOR_CPI); }
  bool okB = sensorB.begin(PIN_CS_B, SENSOR_CPI);
  if(!okB){ delay(100); okB = sensorB.begin(PIN_CS_B, SENSOR_CPI); }

  if(!solverOK) haltBlink(4, "Geometry singular");
  if(!okA)      haltBlink(2, "Sensor A not detected (CS=D7)");
  if(!okB)      haltBlink(3, "Sensor B not detected (CS=D6)");

  Bluefruit.begin(2, 0);   // [MODE] 2 peripheral links: Windows HID + the daemon at once
  Bluefruit.setTxPower(4);
  Bluefruit.setName(BLE_NAME);
  Bluefruit.Periph.setConnectCallback(onConnect);
  Bluefruit.Periph.setDisconnectCallback(onDisconnect);   // [MODE] revert to mouse if daemon drops

  bledis.setManufacturer("DIY");
  bledis.setModel("Dual-Sensor Trackball");
  bledis.begin();

  blehid.begin();
  Bluefruit.Periph.setConnInterval(6, 9);   // AFTER begin() so it sticks; ~11.25 ms
  setupRotationService();                    // [GATT] register before advertising
  startAdv();

  lastScrollMs = millis() - SCROLL_HOLD_MS - 1;
}

void loop(){
  uint32_t nowUs = micros();
  if((uint32_t)(nowUs - lastPollUs) < POLL_INTERVAL_US){
    uint32_t nowMs = millis();
    // LED: disconnected = slow blink (600ms); controller mode = fast blink (200ms); mouse = solid.
    if(!Bluefruit.connected()){
      if(nowMs - lastBlinkMs > 600){ lastBlinkMs=nowMs; ledState=!ledState;
        digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
    } else if(g_controller){
      if(nowMs - lastBlinkMs > 200){ lastBlinkMs=nowMs; ledState=!ledState;
        digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
    } else if(!ledState){ digitalWrite(LED_BUILTIN,LED_STATE_ON); ledState=true; }
    return;
  }
  float dtSec = (float)(uint32_t)(nowUs - lastPollUs) * 1e-6f;   // [IPS] actual poll interval
  lastPollUs = nowUs;

  PMW3389_DATA a = sensorA.readBurst();
  PMW3389_DATA b = sensorB.readBurst();

  // [IPS] measure RAW (pre-cap) peak speed per sensor for spec determination, on-surface only
  if(a.isOnSurface){ float ips=countsToIps(a.dx,a.dy,dtSec);
    if(ips>gPeakIpsA) gPeakIpsA=ips; if(ips>gMaxIpsA) gMaxIpsA=ips; }
  if(b.isOnSurface){ float ips=countsToIps(b.dx,b.dy,dtSec);
    if(ips>gPeakIpsB) gPeakIpsB=ips; if(ips>gMaxIpsB) gMaxIpsB=ips; }

  // [PMW3610] cap at the raw input stage AFTER measuring, so the readout stays raw while the
  // fusion (cube) and mouse see only the limited motion. Same formula as the readout above.
  clampSensorIps(a.dx, a.dy, dtSec);
  clampSensorIps(b.dx, b.dy, dtSec);

  float m[4]={0,0,0,0};
  if(a.isOnSurface && a.isMotion){ m[0]=a.dx; m[1]=a.dy; }
  if(b.isOnSurface && b.isMotion){ m[2]=b.dx; m[3]=b.dy; }
  float wx = pinv[0][0]*m[0]+pinv[0][1]*m[1]+pinv[0][2]*m[2]+pinv[0][3]*m[3];
  float wy = pinv[1][0]*m[0]+pinv[1][1]*m[1]+pinv[1][2]*m[2]+pinv[1][3]*m[3];
  float wz = pinv[2][0]*m[0]+pinv[2][1]*m[1]+pinv[2][2]*m[2]+pinv[2][3]*m[3];

  // [GATT] integrate true ball rotation (radians) this poll. wx/wy/wz are R_counts * radians,
  // so divide by R_COUNTS to get radians. Independent of mouse/scroll mode below.
  gx += wx / R_COUNTS;   // roll  (about ball X)
  gy += wy / R_COUNTS;   // pitch (about ball Y)
  gz += wz / R_COUNTS;   // yaw   (about ball Z)

  uint32_t nowMs=millis();

#if DEBUG_PRINT
  // [IPS] periodic peak raw-speed report: per-sensor peak since last print + session max.
  if(nowMs - lastIpsReportMs >= IPS_REPORT_MS){
    lastIpsReportMs = nowMs;
    Serial.print("peak ips A="); Serial.print(gPeakIpsA,1);
    Serial.print(" B=");         Serial.print(gPeakIpsB,1);
    Serial.print("  | session max A="); Serial.print(gMaxIpsA,1);
    Serial.print(" B=");                Serial.println(gMaxIpsB,1);
    gPeakIpsA = gPeakIpsB = 0.0f;
  }
#endif

  if(!g_controller){
    // ---- mouse mode: locked cursor/scroll mapping (unchanged) ----
    float yawMag=fabsf(wz), horizMag=sqrtf(wx*wx+wy*wy);
    bool yawDom = yawMag > (YAW_DOMINANCE_RATIO*horizMag);
    bool yawAct = yawMag > YAW_DEADZONE;
    bool scrollNow = yawAct && yawDom;
    if(scrollNow) lastScrollMs=nowMs;
    bool scrolling = scrollNow || ((nowMs-lastScrollMs) < (uint32_t)SCROLL_HOLD_MS);
    if(!scrollNow && horizMag>YAW_DEADZONE && horizMag>(YAW_DOMINANCE_RATIO*yawMag)) scrolling=false;

    if(scrolling){ float y=wz*SCROLL_GAIN; if(SCROLL_INVERT) y=-y; accScroll+=y; }
    else {
      float cx = CURSOR_SWAP_XY?wy:wx, cy = CURSOR_SWAP_XY?wx:wy;
      if(CURSOR_INVERT_X) cx=-cx; if(CURSOR_INVERT_Y) cy=-cy;
      accX += cx*CURSOR_GAIN; accY += cy*CURSOR_GAIN;
    }
  } else {
    // ---- controller mode: ball drives the rotation stream only; keep the cursor still ----
    accX = accY = accScroll = 0.0f;
  }

  g_buttons = readButtons();
  if(g_buttons != g_protocolButtons){
    g_protocolButtons = g_buttons;
    g_inputSequence++;                                  // uint16_t wrap is the wire contract
    notifyInputState();                                 // no-op unless at least one client subscribed
  }

  if(Bluefruit.connected() && (nowMs-lastSendMs) >= BLE_SEND_MS){
    lastSendMs = nowMs;
    if(!g_controller){
      // ---- mouse mode: HID buttons / move / scroll (unchanged) ----
      if(g_buttons != lastButtons){
        bool ok = g_buttons ? blehid.mouseButtonPress(g_buttons) : blehid.mouseButtonRelease();
        if(ok) lastButtons = g_buttons;
      }
      int8_t sx=clamp8(accX), sy=clamp8(accY);
      if(sx||sy){ if(blehid.mouseMove(sx,sy)){ accX-=sx; accY-=sy; } }
      int32_t det=(int32_t)(accScroll/SCROLL_DIVISOR);
      int8_t wheel=clamp8((float)det);
      if(wheel){ if(blehid.mouseScroll(wheel)) accScroll-=(float)wheel*SCROLL_DIVISOR; }
    } else if(lastButtons){
      // [MODE] entering controller mode with a button held -> release it so nothing sticks
      if(blehid.mouseButtonRelease()) lastButtons = 0;
    }

    // [GATT] ship the integrated rotation delta (radians) and zero the accumulators.
    // Float accumulators -> exact 1:1; the cube's total rotation tracks the ball
    // regardless of timing. notify() no-ops (returns false) until a client subscribes.
    float rbuf[3] = { gx*ROT_SIGN_X, gy*ROT_SIGN_Y, gz*ROT_SIGN_Z };
    rotationChar.notify(rbuf, sizeof(rbuf));
    gx = gy = gz = 0.0f;
  }
}
