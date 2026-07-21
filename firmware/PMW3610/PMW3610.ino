/*
 * Dual PMW3610 Trackball -> BLE HID Mouse + Astrolabe rotation/input
 * Board: SuperMini nRF52840 via "nRFMicro-like Boards" (Tools → SuperMini nRF52840).
 * That package renumbers GPIOs as D0..D20 / P0_xx / P1_xx — do NOT treat silkscreen
 * "017" as Arduino pin 17 (that mapping is wrong and was blocking BLE bring-up).
 *
 * Sensors (shared half-duplex SDIO + SCLK, separate CS + MOTION):
 *   R: CS=P0.20  MOTION=P0.17   L: CS=P1.00  MOTION=P0.11
 *   SCLK=P0.22   SDIO=P0.24
 * Five-way (active-low, internal pull-ups; SWITCH+ on the PCB is battery power, not switch common):
 *   DOWN=P1.13  CENTER=P1.15  LEFT=P0.02  UP=P0.29  RIGHT=P0.31
 *   (cardinals rotated 90° CW from silk so physical Up/Down/Left/Right match the housing)
 *
 * Advertises as "Astrolabe" and publishes the production five-way snapshot bit map
 * (bit0 Up, bit1 Down, bit2 Left, bit3 Right, bit4 Center). Standalone HID maps
 * Down=LMB, Right=RMB, Center=MMB. Daemon subscription suppresses HID pointer/buttons;
 * with the daemon, those directions are Walk / Fly / mode-toggle bindings, not OS clicks.
 *
 * Power: MOTION/button IRQ opens an active window with 1 kHz dual-sensor polling
 * (XIAO3389 cadence). After MOTION_IDLE_MS with no activity, sensors enter rest and
 * the MCU waits for the next interrupt. No simulated IPS throttling. Ball = 2.0 in.
 */
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <math.h>

#ifndef LED_STATE_ON
#define LED_STATE_ON HIGH
#endif

// SuperMini_nRF52840 variant macros (P0_xx / P1_xx / Dx). Fall back to the same
// Arduino pin numbers if building under a package that only exposes Dx.
#if defined(P0_20)
#define PIN_CS_R        P0_20     // D3  — R/CS
#define PIN_MOTION_R    P0_17     // D2  — RMOTION (active low)
#define PIN_CS_L        P1_00     // D6  — L/CS
#define PIN_MOTION_L    P0_11     // D7  — LMOTION (active low)
#define PIN_SCLK        P0_22     // D4
#define PIN_SDIO        P0_24     // D5
#define PIN_BTN_DOWN    P1_13     // D13
#define PIN_BTN_CENTER  P1_15     // D14
#define PIN_BTN_LEFT    P0_02     // D15
#define PIN_BTN_UP      P0_29     // D16
#define PIN_BTN_RIGHT   P0_31     // D17
#else
#define PIN_CS_R        D3
#define PIN_MOTION_R    D2
#define PIN_CS_L        D6
#define PIN_MOTION_L    D7
#define PIN_SCLK        D4
#define PIN_SDIO        D5
#define PIN_BTN_DOWN    D13
#define PIN_BTN_CENTER  D14
#define PIN_BTN_LEFT      D15
#define PIN_BTN_UP   D16
#define PIN_BTN_RIGHT    D17
#endif

// L = sensor A rows, R = sensor B rows in the dual-sensor solver.
#define SENSOR_L_PHI    140.0f
#define SENSOR_L_THETA  130.0f
#define SENSOR_R_PHI    230.0f
#define SENSOR_R_THETA  130.0f
#define SENSOR_L_MOUNT_DEG  270.0f
#define SENSOR_L_FLIP       1
#define SENSOR_R_MOUNT_DEG  270.0f
#define SENSOR_R_FLIP       1

#define SENSOR_CPI       1600
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

#define BLE_NAME        "Astrolabe"
#define BLE_SEND_MS     7
#define POLL_INTERVAL_US 1000   // 1 kHz while awake (matches XIAO3389)
#define MOTION_IDLE_MS   80     // no motion/button activity -> MCU+sensor sleep
#define DEBOUNCE_MS     8
#define DEBUG_PRINT     1
#define IPS_REPORT_MS   8000
#define HID_DRAIN_MAX   4       // mouseMove packets per send slot (backlog safety)
// Emit CALIB,dxL,dyL,dxR,dyR on USB serial for tools/calibrate_sensor_mounts.py.
// Set to 0 after mount/flip calibration to reduce serial traffic.
#ifndef CALIB_SERIAL
#define CALIB_SERIAL    1
#endif

// ---------------------------------------------------------------------------
// [GATT] Custom 3-axis rotation service (purely additive; HID mouse untouched)
// ---------------------------------------------------------------------------
//   service        2cad0001-6e64-0146-b139-9cf2a4cd57fc
//   rotation       2cad0002-6e64-0146-b139-9cf2a4cd57fc (fixed 12-byte float32 x 3)
//   input state    2cad0003-6e64-0146-b139-9cf2a4cd57fc (protocol v1 snapshot)
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

// astrolabe_5way bit map
#define BTN_BIT_UP      0x01
#define BTN_BIT_DOWN    0x02
#define BTN_BIT_LEFT    0x04
#define BTN_BIT_RIGHT   0x08
#define BTN_BIT_CENTER  0x10

#define BALL_DIAMETER_MM   50.8f   // 2.0 in
#define ROT_SIGN_X         -1.0f
#define ROT_SIGN_Y         -1.0f
#define ROT_SIGN_Z         -1.0f
static const float COUNTS_PER_MM = (float)SENSOR_CPI / 25.4f;
static const float R_COUNTS      = (BALL_DIAMETER_MM * 0.5f) * COUNTS_PER_MM;

// ---------------------------------------------------------------------------
// PMW3610 — half-duplex bit-bang SPI (Mode 3), shared SCLK/SDIO bus
// ---------------------------------------------------------------------------
#define REG_Product_ID          0x00
#define REG_Motion              0x02
#define REG_Delta_X_L           0x03
#define REG_Delta_Y_L           0x04
#define REG_Delta_XY_H          0x05
#define REG_SQUAL               0x06
#define REG_Performance         0x11
#define REG_Burst_Read          0x12
#define REG_Run_Downshift       0x1b
#define REG_Rest1_Rate          0x1c
#define REG_Rest1_Downshift     0x1d
#define REG_Observation1        0x2d
#define REG_Power_Up_Reset      0x3a
#define REG_Not_Product_ID      0x3f
#define REG_Spi_Clk_On_Req      0x41
#define REG_Spi_Page            0x7f
#define REG_Res_Step            0x05   // page 1

#define PMW3610_PRODUCT_ID      0x3E
#define PMW3610_NOT_PRODUCT_ID  0xC1
#define SPI_CLK_ON              0xBA
#define SPI_CLK_OFF             0xB5
#define SPI_PAGE0               0x00
#define SPI_PAGE1               0xFF
#define PERFORMANCE_INIT        0x0D
#define PERFORMANCE_FORCE_AWAKE 0xF0
#define RUN_DOWNSHIFT_INIT      0x04
#define REST1_RATE_INIT         0x04
#define REST1_DOWNSHIFT_INIT    0x0F

// PixArt 3-wire serial port (SPI Mode 3: CPOL=1, CPHA=1).
// PMW3610DM-SUDU lists fSCLK max 2 MHz. Address→data and inter-command gaps match the
// PixArt mouse-sensor serial AC table used by PMW3360/3389/3610 (tSRAD, tSRR, tSWW).
#define T_SCLK_HALF_US   2     // bit period 4 us → 250 kHz (safe margin under 2 MHz)
#define T_NCS_SCLK_US    1     // NCS↓ setup before first SCLK
#define T_SRAD_US        35    // last address SCLK → first read SCLK
#define T_SRR_US         20    // read transaction → next transaction
#define T_SWW_US        120    // write transaction → next transaction
#define T_SCLK_NCS_WR_US 10    // last falling SCLK of write → NCS↑ (tSCLK-NCS write)
#define T_BEXIT_US        4    // NCS↑ hold / shared-bus settle
#define T_CLK_ON_US     300    // after SPI_CLK_ON_REQ = 0xBA

struct PMW3610_DATA {
  bool isMotion;
  bool isOnSurface;
  int16_t dx;
  int16_t dy;
  uint8_t SQUAL;
};

// Shared half-duplex bus helpers (single SDIO + SCLK for both chip-selects).
static void sdioDrive(bool high) {
  pinMode(PIN_SDIO, OUTPUT);
  digitalWrite(PIN_SDIO, high ? HIGH : LOW);
}
static void sdioRelease() {
  pinMode(PIN_SDIO, INPUT);   // Hi-Z so the selected sensor can drive
}
static void sclkIdleHigh() {
  pinMode(PIN_SCLK, OUTPUT);
  digitalWrite(PIN_SCLK, HIGH);
}

// Mode 3 write: idle CLK high; change SDIO on falling edge; sample on rising.
static void bbWriteByte(uint8_t v) {
  sdioDrive(false);
  for (uint8_t i = 0; i < 8; i++) {
    digitalWrite(PIN_SCLK, LOW);
    digitalWrite(PIN_SDIO, (v & 0x80) ? HIGH : LOW);
    delayMicroseconds(T_SCLK_HALF_US);
    digitalWrite(PIN_SCLK, HIGH);
    delayMicroseconds(T_SCLK_HALF_US);
    v <<= 1;
  }
}

// Mode 3 read: SDIO must already be Hi-Z; sample on rising edge.
static uint8_t bbReadByte() {
  uint8_t v = 0;
  for (uint8_t i = 0; i < 8; i++) {
    digitalWrite(PIN_SCLK, LOW);
    delayMicroseconds(T_SCLK_HALF_US);
    digitalWrite(PIN_SCLK, HIGH);
    v = (uint8_t)((v << 1) | (digitalRead(PIN_SDIO) ? 1 : 0));
    delayMicroseconds(T_SCLK_HALF_US);
  }
  return v;
}

static int16_t signExtend12(uint16_t v12) {
  if (v12 & 0x0800) v12 |= 0xF000;
  return (int16_t)v12;
}

class PMW3610 {
public:
  bool begin(uint8_t csPin, uint16_t cpi) {
    _cs = csPin;
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);
    sclkIdleHigh();
    sdioDrive(true);

    // Power-up reset (no SPI clock domain required for 0x3A).
    writeRaw(REG_Power_Up_Reset, 0x5A);
    delay(50);

    if (readRaw(REG_Product_ID) != PMW3610_PRODUCT_ID) return false;
    if (readRaw(REG_Not_Product_ID) != PMW3610_NOT_PRODUCT_ID) return false;

    spiClkOn();
    writeRaw(REG_Observation1, 0x00);
    delay(10);
    (void)readRaw(REG_Observation1);

    // Datasheet: clear motion registers after reset by reading 0x02..0x05.
    for (uint8_t r = REG_Motion; r <= REG_Delta_XY_H; r++) (void)readRaw(r);

    writeRaw(REG_Performance, PERFORMANCE_INIT);
    writeRaw(REG_Run_Downshift, RUN_DOWNSHIFT_INIT);
    writeRaw(REG_Rest1_Rate, REST1_RATE_INIT);
    writeRaw(REG_Rest1_Downshift, REST1_DOWNSHIFT_INIT);
    spiClkOff();

    setCPI(cpi);
    setForceAwake(false);
    return true;
  }

  void setCPI(uint16_t cpi) {
    if (cpi < 200) cpi = 200;
    if (cpi > 3200) cpi = 3200;
    cpi = (cpi / 200) * 200;
    spiClkOn();
    writeRaw(REG_Spi_Page, SPI_PAGE1);
    uint8_t val = readRaw(REG_Res_Step);
    val = (uint8_t)((val & ~0x1F) | (cpi / 200));
    writeRaw(REG_Res_Step, val);
    writeRaw(REG_Spi_Page, SPI_PAGE0);
    spiClkOff();
  }

  void setForceAwake(bool enable) {
    spiClkOn();
    uint8_t val = readRaw(REG_Performance);
    val = (uint8_t)((val & ~0xF0) | (enable ? PERFORMANCE_FORCE_AWAKE : 0x00));
    writeRaw(REG_Performance, val);
    // Leave SPI clock enabled while force-awake so motion polls need no clk-on dance.
    if (!enable) spiClkOff();
  }

  void enterRest() {
    setForceAwake(false);
  }

  // Prefer discrete MOTION..DELTA_XY_H reads over burst: same latch, clearer framing on
  // a shared SDIO bus.
  PMW3610_DATA readMotion() {
    PMW3610_DATA d = {false, true, 0, 0, 0};
    uint8_t motion = readRaw(REG_Motion);
    uint8_t x_l    = readRaw(REG_Delta_X_L);
    uint8_t y_l    = readRaw(REG_Delta_Y_L);
    uint8_t xy_h   = readRaw(REG_Delta_XY_H);
    d.isMotion = (motion & 0x80) != 0;
    d.isOnSurface = true;
    // PMW3610 DELTA_XY_H (0x05): bits[7:4]=Delta_X[11:8], bits[3:0]=Delta_Y[11:8]
    d.dx = signExtend12((uint16_t)(((xy_h & 0xF0) << 4) | x_l));
    d.dy = signExtend12((uint16_t)(((xy_h & 0x0F) << 8) | y_l));
    d.SQUAL = 0;
    return d;
  }

private:
  uint8_t _cs = 0xFF;

  void csLow() {
    digitalWrite(_cs, LOW);
    delayMicroseconds(T_NCS_SCLK_US);
  }
  void csHigh() {
    digitalWrite(_cs, HIGH);
    delayMicroseconds(T_BEXIT_US);
  }

  void spiClkOn() {
    writeRaw(REG_Spi_Clk_On_Req, SPI_CLK_ON);
    delayMicroseconds(T_CLK_ON_US);
  }
  void spiClkOff() {
    writeRaw(REG_Spi_Clk_On_Req, SPI_CLK_OFF);
  }

  uint8_t readRaw(uint8_t addr) {
    sclkIdleHigh();
    csLow();
    sdioDrive(true);
    bbWriteByte(addr & 0x7F);
    // Critical: release SDIO to Hi-Z BEFORE tSRAD so the sensor can drive the bus.
    sdioRelease();
    delayMicroseconds(T_SRAD_US);
    uint8_t data = bbReadByte();
    csHigh();
    sdioDrive(true);
    delayMicroseconds(T_SRR_US);
    return data;
  }

  void writeRaw(uint8_t addr, uint8_t data) {
    sclkIdleHigh();
    csLow();
    sdioDrive(true);
    bbWriteByte(addr | 0x80);
    bbWriteByte(data);
    // tSCLK-NCS(write): min 10 us from last falling SCLK to NCS rising.
    delayMicroseconds(T_SCLK_NCS_WR_US);
    csHigh();
    sdioDrive(true);
    delayMicroseconds(T_SWW_US);
  }
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
  float nL[3] = { sinf(SENSOR_L_THETA*D2R)*cosf(SENSOR_L_PHI*D2R),
                  sinf(SENSOR_L_THETA*D2R)*sinf(SENSOR_L_PHI*D2R),
                  cosf(SENSOR_L_THETA*D2R) };
  float nR[3] = { sinf(SENSOR_R_THETA*D2R)*cosf(SENSOR_R_PHI*D2R),
                  sinf(SENSOR_R_THETA*D2R)*sinf(SENSOR_R_PHI*D2R),
                  cosf(SENSOR_R_THETA*D2R) };
  float lDx[3],lDy[3],rDx[3],rDy[3];
  sensorAxes(nL, SENSOR_L_MOUNT_DEG, SENSOR_L_FLIP, lDx, lDy);
  sensorAxes(nR, SENSOR_R_MOUNT_DEG, SENSOR_R_FLIP, rDx, rDy);
  cross3(nL, lDx, A[0]); cross3(nL, lDy, A[1]);
  cross3(nR, rDx, A[2]); cross3(nR, rDy, A[3]);
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
BLEService        rotationService(ROT_SERVICE_UUID);
BLECharacteristic rotationChar(ROT_CHAR_UUID);
BLECharacteristic inputStateChar(INPUT_CHAR_UUID);
PMW3610 sensorL, sensorR;

float    accX=0, accY=0, accScroll=0;
float    gx=0, gy=0, gz=0;
uint8_t  lastHidButtons=0;
uint8_t  g_protocolButtons=0;
uint8_t  g_hidButtons=0;
uint16_t g_inputSequence=0;
uint32_t lastScrollMs=0, lastSendMs=0, lastBlinkMs=0, lastIpsReportMs=0;
uint32_t lastPollUs=0, lastActivityMs=0;
bool     ledState=false;
bool     g_sensorsAwake=false;
bool     g_okL=false, g_okR=false;
bool     g_debouncePending=false;

volatile bool g_controller = false;
uint16_t      g_ctrlConn   = BLE_CONN_HANDLE_INVALID;
volatile bool g_motionWake = false;
volatile bool g_buttonWake = false;

float    gPeakIpsL=0, gPeakIpsR=0, gMaxIpsL=0, gMaxIpsR=0;

static int8_t clamp8(float v){ return v>127?127:(v<-127?-127:(int8_t)v); }

static inline float countsToIps(int16_t dx, int16_t dy, float dtSec){
  if(dtSec <= 0.0f) return 0.0f;
  return sqrtf((float)dx*dx + (float)dy*dy) / (float)SENSOR_CPI / dtSec;
}

static inline bool motionPinActive(){
  return (g_okL && digitalRead(PIN_MOTION_L) == LOW)
      || (g_okR && digitalRead(PIN_MOTION_R) == LOW);
}

static void sensorsForceAwake(){
  if(g_sensorsAwake) return;
  if(g_okL) sensorL.setForceAwake(true);
  if(g_okR) sensorR.setForceAwake(true);
  g_sensorsAwake = true;
}

static void sensorsEnterRest(){
  if(!g_sensorsAwake) return;
  if(g_okL) sensorL.enterRest();
  if(g_okR) sensorR.enterRest();
  g_sensorsAwake = false;
}

static void clearMotionAccumulators(){
  gx = gy = gz = 0.0f;
  accX = accY = accScroll = 0.0f;
}

static void onConnect(uint16_t conn_handle){
  BLEConnection* c = Bluefruit.Connection(conn_handle);
  c->requestConnectionParameter(9);
  // Drop any pre-connection residue so the first reports are not a backlog dump.
  clearMotionAccumulators();
  lastActivityMs = millis();
  sensorsForceAwake();
#if DEBUG_PRINT
  uint16_t iv = c->getConnectionInterval();
  Serial.print("conn interval = "); Serial.print(iv*1.25f,2);
  Serial.print(" ms (~"); Serial.print(1000.0f/(iv*1.25f),0); Serial.println(" Hz)");
#endif
}

static void onDisconnect(uint16_t conn_handle, uint8_t reason){
  (void)reason;
  if(conn_handle == g_ctrlConn){ g_controller = false; g_ctrlConn = BLE_CONN_HANDLE_INVALID; }
  clearMotionAccumulators();
  if(!Bluefruit.connected()){
    lastHidButtons = 0;
    sensorsEnterRest();
  }
}

static void rotCccdCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint16_t value){
  (void)value;
  if(chr->notifyEnabled(conn_hdl)){ g_controller = true;  g_ctrlConn = conn_hdl; }
  else if(conn_hdl == g_ctrlConn) { g_controller = false; g_ctrlConn = BLE_CONN_HANDLE_INVALID; }
}

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

static void inputCccdCallback(uint16_t conn_hdl, BLECharacteristic* chr, uint16_t value){
  (void)value;
  if(chr->notifyEnabled(conn_hdl)) notifyInputState(conn_hdl);
}

// Non-fatal warn: blink then return so BLE HID still advertises.
static void warnBlink(uint8_t blinks, const char *msg){
  pinMode(LED_BUILTIN, OUTPUT);
#if DEBUG_PRINT
  Serial.println(msg);
#endif
  for(uint8_t n=0;n<3;n++){
    for(uint8_t i=0;i<blinks;i++){ digitalWrite(LED_BUILTIN,LED_STATE_ON);delay(150);
      digitalWrite(LED_BUILTIN,!LED_STATE_ON);delay(200);} delay(400);
  }
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

static void motionIsr(){ g_motionWake = true; }
static void buttonIsr(){ g_buttonWake = true; }

// Active-low five-way with internal pull-ups. Publish the full observed bitset.
// HID standalone: Down=LMB, Right=RMB, Center=MMB (Up/Left are protocol-only).
// Returns true while any channel is still inside its debounce window (keep polling).
static bool readButtons(uint8_t &protocolOut, uint8_t &hidOut){
  static const uint8_t pins[5]  = {PIN_BTN_UP, PIN_BTN_DOWN, PIN_BTN_LEFT, PIN_BTN_RIGHT, PIN_BTN_CENTER};
  static const uint8_t masks[5] = {BTN_BIT_UP, BTN_BIT_DOWN, BTN_BIT_LEFT, BTN_BIT_RIGHT, BTN_BIT_CENTER};
  static bool stable[5]={0}, lastRaw[5]={0}; static uint32_t tChange[5]={0};
  uint32_t nowMs=millis(); uint8_t protocol=0; bool pending=false;
  for(uint8_t i=0;i<5;i++){
    bool raw=(digitalRead(pins[i])==LOW);
    if(raw!=lastRaw[i]){lastRaw[i]=raw;tChange[i]=nowMs;}
    if(raw!=stable[i]){
      if((nowMs-tChange[i])>=DEBOUNCE_MS) stable[i]=raw;
      else pending=true;
    }
    if(stable[i]) protocol|=masks[i];
  }
  protocolOut = protocol;
  hidOut = 0;
  if(protocol & BTN_BIT_DOWN)   hidOut |= 0x01;
  if(protocol & BTN_BIT_RIGHT)  hidOut |= 0x02;
  if(protocol & BTN_BIT_CENTER) hidOut |= 0x04;
  return pending;
}

static void setupRotationService(){
  rotationService.begin();
  rotationChar.setProperties(CHR_PROPS_NOTIFY);
  rotationChar.setPermission(SECMODE_OPEN, SECMODE_NO_ACCESS);
  rotationChar.setFixedLen(12);
  rotationChar.setCccdWriteCallback(rotCccdCallback);
  rotationChar.begin();
  inputStateChar.setProperties(CHR_PROPS_NOTIFY);
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
  Bluefruit.ScanResponse.addService(rotationService);
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);
}

static void updateLed(uint32_t nowMs){
  if(!Bluefruit.connected()){
    if(nowMs - lastBlinkMs > 600){ lastBlinkMs=nowMs; ledState=!ledState;
      digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
  } else if(g_controller){
    if(nowMs - lastBlinkMs > 200){ lastBlinkMs=nowMs; ledState=!ledState;
      digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
  } else if(!ledState){ digitalWrite(LED_BUILTIN,LED_STATE_ON); ledState=true; }
}

static void flushOutputs(uint32_t nowMs){
  if(!Bluefruit.connected()) return;
  if((nowMs - lastSendMs) < BLE_SEND_MS) return;

  bool pendingMotionReport = (gx!=0.0f || gy!=0.0f || gz!=0.0f);
  bool pendingHid = !g_controller && (accX!=0.0f || accY!=0.0f || accScroll!=0.0f
                                      || g_hidButtons != lastHidButtons);
  bool needHidRelease = g_controller && lastHidButtons;
  if(!pendingMotionReport && !pendingHid && !needHidRelease) return;

  lastSendMs = nowMs;
  if(!g_controller){
    if(g_hidButtons != lastHidButtons){
      bool ok = g_hidButtons ? blehid.mouseButtonPress(g_hidButtons) : blehid.mouseButtonRelease();
      if(ok) lastHidButtons = g_hidButtons;
    }
    // Drain backlog in-slot so a rare stall cannot become a long constant-speed line.
    for(uint8_t n=0;n<HID_DRAIN_MAX;n++){
      int8_t sx=clamp8(accX), sy=clamp8(accY);
      if(!sx && !sy) break;
      if(!blehid.mouseMove(sx,sy)) break;
      accX-=sx; accY-=sy;
    }
    int32_t det=(int32_t)(accScroll/SCROLL_DIVISOR);
    int8_t wheel=clamp8((float)det);
    if(wheel){ if(blehid.mouseScroll(wheel)) accScroll-=(float)wheel*SCROLL_DIVISOR; }
  } else if(lastHidButtons){
    if(blehid.mouseButtonRelease()) lastHidButtons = 0;
  }

  if(pendingMotionReport){
    float rbuf[3] = { gx*ROT_SIGN_X, gy*ROT_SIGN_Y, gz*ROT_SIGN_Z };
    // Retain deltas if SoftDevice rejects the notify while a daemon is subscribed.
    if(rotationChar.notify(rbuf, sizeof(rbuf)) || !g_controller){
      gx = gy = gz = 0.0f;
    }
  }
}

// Race-safe idle: re-check wake flags after masking IRQs so an edge cannot be lost
// between the predicate and waitForEvent().
static void trySleepUntilInterrupt(uint32_t nowMs){
  bool pendingOut = (gx!=0.0f || gy!=0.0f || gz!=0.0f)
                 || (!g_controller && (accX!=0.0f || accY!=0.0f || accScroll!=0.0f
                                       || g_hidButtons != lastHidButtons))
                 || (g_controller && lastHidButtons);
  if(pendingOut || g_debouncePending) return;
  if((nowMs - lastActivityMs) < MOTION_IDLE_MS) return;
  if(g_motionWake || g_buttonWake || motionPinActive()) return;

  sensorsEnterRest();
  updateLed(nowMs);

  noInterrupts();
  if(!g_motionWake && !g_buttonWake && !motionPinActive()){
    interrupts();
    waitForEvent();
  } else {
    interrupts();
  }
}

void setup(){
  pinMode(PIN_CS_L, OUTPUT); digitalWrite(PIN_CS_L, HIGH);
  pinMode(PIN_CS_R, OUTPUT); digitalWrite(PIN_CS_R, HIGH);
  pinMode(PIN_SCLK, OUTPUT); digitalWrite(PIN_SCLK, HIGH);
  pinMode(PIN_SDIO, OUTPUT); digitalWrite(PIN_SDIO, HIGH);

  pinMode(PIN_MOTION_L, INPUT_PULLUP);
  pinMode(PIN_MOTION_R, INPUT_PULLUP);
  pinMode(PIN_BTN_LEFT,   INPUT_PULLUP);
  pinMode(PIN_BTN_RIGHT,  INPUT_PULLUP);
  pinMode(PIN_BTN_UP,     INPUT_PULLUP);
  pinMode(PIN_BTN_DOWN,   INPUT_PULLUP);
  pinMode(PIN_BTN_CENTER, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, !LED_STATE_ON);

#if DEBUG_PRINT
  Serial.begin(115200);
  for(uint32_t t0=millis(); !Serial && (millis()-t0)<2000; ) delay(10);
  Serial.println("PMW3610 Astrolabe bring-up");
  Serial.print("pins CS_L="); Serial.print((int)PIN_CS_L);
  Serial.print(" CS_R="); Serial.print((int)PIN_CS_R);
  Serial.print(" SCLK="); Serial.print((int)PIN_SCLK);
  Serial.print(" SDIO="); Serial.println((int)PIN_SDIO);
#endif

  // BLE first so a sensor fault cannot prevent HID advertising.
  Bluefruit.begin(2, 0);
  Bluefruit.setTxPower(4);
  Bluefruit.setName(BLE_NAME);
  Bluefruit.Periph.setConnectCallback(onConnect);
  Bluefruit.Periph.setDisconnectCallback(onDisconnect);

  bledis.setManufacturer("Astrolabe");
  bledis.setModel("Dual-PMW3610 Trackball");
  bledis.begin();

  blehid.begin();
  Bluefruit.Periph.setConnInterval(6, 9);
  setupRotationService();
  startAdv();
#if DEBUG_PRINT
  Serial.println("BLE advertising as Astrolabe");
#endif

  bool solverOK = buildSolver();
  if(!solverOK) haltBlink(4, "Geometry singular");

  g_okL = sensorL.begin(PIN_CS_L, SENSOR_CPI);
  if(!g_okL){ delay(100); g_okL = sensorL.begin(PIN_CS_L, SENSOR_CPI); }
  g_okR = sensorR.begin(PIN_CS_R, SENSOR_CPI);
  if(!g_okR){ delay(100); g_okR = sensorR.begin(PIN_CS_R, SENSOR_CPI); }

  if(!g_okL) warnBlink(2, "Sensor L not detected (CS=P1.00 / D6)");
  if(!g_okR) warnBlink(3, "Sensor R not detected (CS=P0.20 / D3)");
#if DEBUG_PRINT
  Serial.print("sensors L="); Serial.print(g_okL ? "ok" : "FAIL");
  Serial.print(" R="); Serial.println(g_okR ? "ok" : "FAIL");
#endif

  if(g_okL) attachInterrupt(digitalPinToInterrupt(PIN_MOTION_L), motionIsr, FALLING);
  if(g_okR) attachInterrupt(digitalPinToInterrupt(PIN_MOTION_R), motionIsr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_LEFT),   buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_RIGHT),  buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_UP),     buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_DOWN),   buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_CENTER), buttonIsr, CHANGE);

  lastScrollMs = millis() - SCROLL_HOLD_MS - 1;
  lastPollUs = micros();
  lastActivityMs = millis();
}

void loop(){
  uint32_t nowMs = millis();

  // MOTION/button IRQ (or still-asserted MOTION) opens an active-poll window.
  if(g_motionWake || g_buttonWake || motionPinActive()){
    lastActivityMs = nowMs;
    sensorsForceAwake();
  }

  bool inActiveWindow = ((nowMs - lastActivityMs) < MOTION_IDLE_MS)
                     || g_debouncePending
                     || motionPinActive()
                     || g_motionWake
                     || g_buttonWake;

  if(!inActiveWindow){
    // Discard unsent motion while disconnected so sleep cannot be blocked by a cache.
    if(!Bluefruit.connected()) clearMotionAccumulators();
    trySleepUntilInterrupt(nowMs);
    return;
  }

  // ---- active: 1 kHz dual-sensor poll (XIAO3389 cadence) ----
  uint32_t nowUs = micros();
  if((uint32_t)(nowUs - lastPollUs) < POLL_INTERVAL_US){
    // Between polls: still service buttons/BLE/LED at full loop rate.
    uint8_t protocolButtons=0, hidButtons=0;
    g_debouncePending = readButtons(protocolButtons, hidButtons);
    g_hidButtons = hidButtons;
    if(protocolButtons != g_protocolButtons){
      g_protocolButtons = protocolButtons;
      g_inputSequence++;
      notifyInputState();
      lastActivityMs = nowMs;
    }
    flushOutputs(nowMs);
    updateLed(nowMs);
    return;
  }

  float dtSec = (float)(uint32_t)(nowUs - lastPollUs) * 1e-6f;
  lastPollUs = nowUs;
  // Consume edge flags only once we are in the poll path (after wake handling).
  g_motionWake = false;
  g_buttonWake = false;

  sensorsForceAwake();

  // Always burst-read both sensors while awake — matches XIAO3389 and keeps the
  // dual-sensor least-squares solve fully determined. Gate contributions on isMotion.
  PMW3610_DATA a = {false, true, 0, 0, 0};
  PMW3610_DATA b = {false, true, 0, 0, 0};
  if(g_okL) a = sensorL.readMotion();
  if(g_okR) b = sensorR.readMotion();

  bool sawMotion = a.isMotion || b.isMotion;
  if(sawMotion) lastActivityMs = nowMs;

#if CALIB_SERIAL
  // Raw dual-sensor bursts for residual mount search (tools/calibrate_sensor_mounts.py).
  // Emit even when disconnected so capture does not need a BLE host.
  if(sawMotion){
    Serial.print("CALIB,");
    Serial.print(a.dx); Serial.print(',');
    Serial.print(a.dy); Serial.print(',');
    Serial.print(b.dx); Serial.print(',');
    Serial.println(b.dy);
  }
#endif

#if DEBUG_PRINT
  if(a.isMotion){ float ips=countsToIps(a.dx,a.dy,dtSec);
    if(ips>gPeakIpsL) gPeakIpsL=ips; if(ips>gMaxIpsL) gMaxIpsL=ips; }
  if(b.isMotion){ float ips=countsToIps(b.dx,b.dy,dtSec);
    if(ips>gPeakIpsR) gPeakIpsR=ips; if(ips>gMaxIpsR) gMaxIpsR=ips; }
  if(nowMs - lastIpsReportMs >= IPS_REPORT_MS){
    lastIpsReportMs = nowMs;
    Serial.print("peak ips L="); Serial.print(gPeakIpsL,1);
    Serial.print(" R=");         Serial.print(gPeakIpsR,1);
    Serial.print("  | session max L="); Serial.print(gMaxIpsL,1);
    Serial.print(" R=");                Serial.println(gMaxIpsR,1);
    gPeakIpsL = gPeakIpsR = 0.0f;
  }
#endif

  // Only fuse/accumulate while a host is connected — prevents reconnect dumps.
  if(Bluefruit.connected() && sawMotion){
    float m[4]={0,0,0,0};
    if(a.isMotion){ m[0]=a.dx; m[1]=a.dy; }
    if(b.isMotion){ m[2]=b.dx; m[3]=b.dy; }
    float wx = pinv[0][0]*m[0]+pinv[0][1]*m[1]+pinv[0][2]*m[2]+pinv[0][3]*m[3];
    float wy = pinv[1][0]*m[0]+pinv[1][1]*m[1]+pinv[1][2]*m[2]+pinv[1][3]*m[3];
    float wz = pinv[2][0]*m[0]+pinv[2][1]*m[1]+pinv[2][2]*m[2]+pinv[2][3]*m[3];

    gx += wx / R_COUNTS;
    gy += wy / R_COUNTS;
    gz += wz / R_COUNTS;

    if(!g_controller){
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
      accX = accY = accScroll = 0.0f;
    }
  }

  uint8_t protocolButtons=0, hidButtons=0;
  g_debouncePending = readButtons(protocolButtons, hidButtons);
  g_hidButtons = hidButtons;
  if(protocolButtons != g_protocolButtons){
    g_protocolButtons = protocolButtons;
    g_inputSequence++;
    notifyInputState();
    lastActivityMs = nowMs;
#if DEBUG_PRINT
    Serial.print("BTN");
    if(protocolButtons & BTN_BIT_UP)     Serial.print(" UP");
    if(protocolButtons & BTN_BIT_DOWN)   Serial.print(" DOWN");
    if(protocolButtons & BTN_BIT_LEFT)   Serial.print(" LEFT");
    if(protocolButtons & BTN_BIT_RIGHT)  Serial.print(" RIGHT");
    if(protocolButtons & BTN_BIT_CENTER) Serial.print(" CENTER");
    if(!protocolButtons) Serial.print(" (none)");
    Serial.print("  hid=0x"); Serial.println(hidButtons, HEX);
#endif
  }

  flushOutputs(nowMs);
  updateLed(nowMs);
}
