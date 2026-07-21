/*
 * Dual PMW3610 Trackball -> BLE HID Mouse + Astrolabe rotation/input
 * Board: SuperMini nRF52840 (Nice!Nano-compatible), Adafruit nRF52 Arduino / Bluefruit.
 * Use a Nice!Nano or SuperMini board package that defines PIN_0xx / PIN_1xx macros.
 *
 * Sensors (shared half-duplex SDIO + SCLK, separate CS + MOTION):
 *   R: CS=P0.20  MOTION=P0.17   L: CS=P1.00  MOTION=P0.11
 *   SCLK=P0.22   SDIO=P0.24
 * Five-way (active-low, internal pull-ups; SWITCH+ on the PCB is battery power, not switch common):
 *   LEFT=P1.13  CENTER=P1.15  UP=P0.02  RIGHT=P0.29  DOWN=P0.31
 *
 * Advertises as "Astrolabe" and publishes the production five-way snapshot bit map
 * (bit0 Up, bit1 Down, bit2 Left, bit3 Right, bit4 Center). Standalone HID maps
 * Down=LMB, Right=RMB, Center=MMB. Daemon subscription suppresses HID pointer/buttons.
 *
 * Interrupt-driven sensor reads and button wakeups; MCU waits for events when idle
 * (battery-oriented). No simulated IPS throttling. Ball diameter = 2.0 in.
 */
#include <Adafruit_TinyUSB.h>
#include <bluefruit.h>
#include <math.h>

#ifndef LED_STATE_ON
#define LED_STATE_ON LOW
#endif

// Arduino pin aliases for Nice!Nano / SuperMini silkscreen P-numbers.
#ifndef PIN_017
#define PIN_017 17
#endif
#ifndef PIN_020
#define PIN_020 20
#endif
#ifndef PIN_022
#define PIN_022 22
#endif
#ifndef PIN_024
#define PIN_024 24
#endif
#ifndef PIN_100
#define PIN_100 32
#endif
#ifndef PIN_011
#define PIN_011 11
#endif
#ifndef PIN_002
#define PIN_002 2
#endif
#ifndef PIN_029
#define PIN_029 29
#endif
#ifndef PIN_031
#define PIN_031 31
#endif
#ifndef PIN_113
#define PIN_113 45
#endif
#ifndef PIN_115
#define PIN_115 47
#endif

#define PIN_CS_R        PIN_020   // R/CS
#define PIN_MOTION_R    PIN_017   // RMOTION (active low)
#define PIN_CS_L        PIN_100   // L/CS
#define PIN_MOTION_L    PIN_011   // LMOTION (active low)
#define PIN_SCLK        PIN_022
#define PIN_SDIO        PIN_024

#define PIN_BTN_LEFT    PIN_113
#define PIN_BTN_CENTER  PIN_115
#define PIN_BTN_UP      PIN_002
#define PIN_BTN_RIGHT   PIN_029
#define PIN_BTN_DOWN    PIN_031

// L = sensor A rows, R = sensor B rows in the dual-sensor solver.
#define SENSOR_L_PHI    140.0f
#define SENSOR_L_THETA  130.0f
#define SENSOR_R_PHI    230.0f
#define SENSOR_R_THETA  130.0f
#define SENSOR_L_MOUNT_DEG  90.0f
#define SENSOR_L_FLIP       0
#define SENSOR_R_MOUNT_DEG  90.0f
#define SENSOR_R_FLIP       0

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
#define DEBOUNCE_MS     8
#define DEBUG_PRINT     1
#define IPS_REPORT_MS   8000

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

struct PMW3610_DATA {
  bool isMotion;
  bool isOnSurface;
  int16_t dx;
  int16_t dy;
  uint8_t SQUAL;
};

class PMW3610 {
public:
  bool begin(uint8_t csPin, uint16_t cpi) {
    _cs = csPin;
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);
    pinMode(PIN_SCLK, OUTPUT);
    digitalWrite(PIN_SCLK, HIGH);   // Mode 3 idle high
    pinMode(PIN_SDIO, OUTPUT);
    digitalWrite(PIN_SDIO, HIGH);

    writeReg(REG_Power_Up_Reset, 0x5A);
    delay(50);

    if (readReg(REG_Product_ID) != PMW3610_PRODUCT_ID) return false;
    if (readReg(REG_Not_Product_ID) != PMW3610_NOT_PRODUCT_ID) return false;

    spiClkOn();
    writeReg(REG_Observation1, 0x00);
    delay(10);
    uint8_t obs = readReg(REG_Observation1);
    if ((obs & 0x0F) != 0x0F) {
      // Observation sticky bits should settle after reset; retry once.
      delay(10);
      obs = readReg(REG_Observation1);
      if ((obs & 0x0F) != 0x0F) return false;
    }

    for (uint8_t r = REG_Motion; r <= REG_Delta_XY_H; r++) (void)readReg(r);

    writeReg(REG_Performance, PERFORMANCE_INIT);
    writeReg(REG_Run_Downshift, RUN_DOWNSHIFT_INIT);
    writeReg(REG_Rest1_Rate, REST1_RATE_INIT);
    writeReg(REG_Rest1_Downshift, REST1_DOWNSHIFT_INIT);
    spiClkOff();

    setCPI(cpi);
    // Stay awake while the host is using the device; rest modes resume when disconnected.
    setForceAwake(true);
    return true;
  }

  void setCPI(uint16_t cpi) {
    if (cpi < 200) cpi = 200;
    if (cpi > 3200) cpi = 3200;
    cpi = (cpi / 200) * 200;
    spiClkOn();
    writeReg(REG_Spi_Page, SPI_PAGE1);
    uint8_t val = readReg(REG_Res_Step);
    val = (uint8_t)((val & ~0x1F) | (cpi / 200));
    writeReg(REG_Res_Step, val);
    writeReg(REG_Spi_Page, SPI_PAGE0);
    spiClkOff();
  }

  void setForceAwake(bool enable) {
    spiClkOn();
    uint8_t val = readReg(REG_Performance);
    val = (uint8_t)((val & ~0xF0) | (enable ? PERFORMANCE_FORCE_AWAKE : 0x00));
    writeReg(REG_Performance, val);
    spiClkOff();
  }

  void enterRest() {
    setForceAwake(false);
  }

  PMW3610_DATA readBurst() {
    PMW3610_DATA d = {false, true, 0, 0, 0};
    uint8_t buf[5];
    csLow();
    writeByte(REG_Burst_Read);
    delayMicroseconds(5);
    pinMode(PIN_SDIO, INPUT);
    for (uint8_t i = 0; i < 5; i++) buf[i] = readByte();
    pinMode(PIN_SDIO, OUTPUT);
    digitalWrite(PIN_SDIO, HIGH);
    csHigh();

    d.isMotion = (buf[0] & 0x80) != 0;
    // PMW3610 has no dedicated lift bit like PMW3389; treat as on-surface when reporting.
    d.isOnSurface = true;
    int16_t x = (int16_t)(((uint16_t)(buf[3] & 0x0F) << 8) | buf[1]);
    int16_t y = (int16_t)(((uint16_t)(buf[3] & 0xF0) << 4) | buf[2]);
    if (x & 0x0800) x |= (int16_t)0xF000;
    if (y & 0x0800) y |= (int16_t)0xF000;
    d.dx = x;
    d.dy = y;
    d.SQUAL = buf[4];
    return d;
  }

  uint8_t readReg(uint8_t addr) {
    csLow();
    writeByte(addr & 0x7F);
    delayMicroseconds(5);
    pinMode(PIN_SDIO, INPUT);
    uint8_t data = readByte();
    pinMode(PIN_SDIO, OUTPUT);
    digitalWrite(PIN_SDIO, HIGH);
    csHigh();
    delayMicroseconds(1);
    return data;
  }

  void writeReg(uint8_t addr, uint8_t data) {
    csLow();
    writeByte(addr | 0x80);
    writeByte(data);
    csHigh();
    delayMicroseconds(20);
  }

private:
  uint8_t _cs = 0xFF;

  void spiClkOn()  { writeReg(REG_Spi_Clk_On_Req, SPI_CLK_ON);  delayMicroseconds(300); }
  void spiClkOff() { writeReg(REG_Spi_Clk_On_Req, SPI_CLK_OFF); }

  void csLow()  { digitalWrite(_cs, LOW);  delayMicroseconds(1); }
  void csHigh() { digitalWrite(_cs, HIGH); delayMicroseconds(1); }

  // SPI Mode 3 bit-bang: idle CLK high; change data on falling, sample on rising.
  void writeByte(uint8_t v) {
    for (uint8_t i = 0; i < 8; i++) {
      digitalWrite(PIN_SCLK, LOW);
      digitalWrite(PIN_SDIO, (v & 0x80) ? HIGH : LOW);
      delayMicroseconds(1);
      digitalWrite(PIN_SCLK, HIGH);
      delayMicroseconds(1);
      v <<= 1;
    }
  }

  uint8_t readByte() {
    uint8_t v = 0;
    for (uint8_t i = 0; i < 8; i++) {
      digitalWrite(PIN_SCLK, LOW);
      delayMicroseconds(1);
      digitalWrite(PIN_SCLK, HIGH);
      v = (uint8_t)((v << 1) | (digitalRead(PIN_SDIO) ? 1 : 0));
      delayMicroseconds(1);
    }
    return v;
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
uint32_t lastMotionUs=0;
bool     ledState=false;
bool     g_sensorsAwake=true;

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

static void onConnect(uint16_t conn_handle){
  BLEConnection* c = Bluefruit.Connection(conn_handle);
  c->requestConnectionParameter(9);
  gx = gy = gz = 0.0f;
  if(!g_sensorsAwake){ sensorL.setForceAwake(true); sensorR.setForceAwake(true); g_sensorsAwake=true; }
#if DEBUG_PRINT
  delay(50);
  uint16_t iv = c->getConnectionInterval();
  Serial.print("conn interval = "); Serial.print(iv*1.25f,2);
  Serial.print(" ms (~"); Serial.print(1000.0f/(iv*1.25f),0); Serial.println(" Hz)");
#endif
}

static void onDisconnect(uint16_t conn_handle, uint8_t reason){
  (void)reason;
  if(conn_handle == g_ctrlConn){ g_controller = false; g_ctrlConn = BLE_CONN_HANDLE_INVALID; }
  if(!Bluefruit.connected()){
    sensorL.enterRest();
    sensorR.enterRest();
    g_sensorsAwake = false;
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
static void readButtons(uint8_t &protocolOut, uint8_t &hidOut){
  static const uint8_t pins[5]  = {PIN_BTN_UP, PIN_BTN_DOWN, PIN_BTN_LEFT, PIN_BTN_RIGHT, PIN_BTN_CENTER};
  static const uint8_t masks[5] = {BTN_BIT_UP, BTN_BIT_DOWN, BTN_BIT_LEFT, BTN_BIT_RIGHT, BTN_BIT_CENTER};
  static bool stable[5]={0}, lastRaw[5]={0}; static uint32_t tChange[5]={0};
  uint32_t nowMs=millis(); uint8_t protocol=0;
  for(uint8_t i=0;i<5;i++){
    bool raw=(digitalRead(pins[i])==LOW);
    if(raw!=lastRaw[i]){lastRaw[i]=raw;tChange[i]=nowMs;}
    if(raw!=stable[i] && (nowMs-tChange[i])>=DEBOUNCE_MS) stable[i]=raw;
    if(stable[i]) protocol|=masks[i];
  }
  protocolOut = protocol;
  hidOut = 0;
  if(protocol & BTN_BIT_DOWN)   hidOut |= 0x01;
  if(protocol & BTN_BIT_RIGHT)  hidOut |= 0x02;
  if(protocol & BTN_BIT_CENTER) hidOut |= 0x04;
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
#endif

  bool solverOK = buildSolver();

  bool okL = sensorL.begin(PIN_CS_L, SENSOR_CPI);
  if(!okL){ delay(100); okL = sensorL.begin(PIN_CS_L, SENSOR_CPI); }
  bool okR = sensorR.begin(PIN_CS_R, SENSOR_CPI);
  if(!okR){ delay(100); okR = sensorR.begin(PIN_CS_R, SENSOR_CPI); }

  if(!solverOK) haltBlink(4, "Geometry singular");
  if(!okL)      haltBlink(2, "Sensor L not detected (CS=P1.00)");
  if(!okR)      haltBlink(3, "Sensor R not detected (CS=P0.20)");

  attachInterrupt(digitalPinToInterrupt(PIN_MOTION_L), motionIsr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_MOTION_R), motionIsr, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_LEFT),   buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_RIGHT),  buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_UP),     buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_DOWN),   buttonIsr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_BTN_CENTER), buttonIsr, CHANGE);

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

  lastScrollMs = millis() - SCROLL_HOLD_MS - 1;
  lastMotionUs = micros();
}

void loop(){
  uint32_t nowMs = millis();
  bool motionPending = g_motionWake
                    || (digitalRead(PIN_MOTION_L) == LOW)
                    || (digitalRead(PIN_MOTION_R) == LOW);
  g_motionWake = false;
  g_buttonWake = false;

  if(motionPending){
    uint32_t nowUs = micros();
    float dtSec = (float)(uint32_t)(nowUs - lastMotionUs) * 1e-6f;
    if(dtSec < 0.0002f) dtSec = 0.0002f;   // avoid huge ips spikes on back-to-back IRQs
    lastMotionUs = nowUs;

    if(!g_sensorsAwake){
      sensorL.setForceAwake(true);
      sensorR.setForceAwake(true);
      g_sensorsAwake = true;
    }

    PMW3610_DATA a = {false, true, 0, 0, 0};
    PMW3610_DATA b = {false, true, 0, 0, 0};
    if(digitalRead(PIN_MOTION_L) == LOW) a = sensorL.readBurst();
    if(digitalRead(PIN_MOTION_R) == LOW) b = sensorR.readBurst();

#if DEBUG_PRINT
    if(a.isMotion){ float ips=countsToIps(a.dx,a.dy,dtSec);
      if(ips>gPeakIpsL) gPeakIpsL=ips; if(ips>gMaxIpsL) gMaxIpsL=ips; }
    if(b.isMotion){ float ips=countsToIps(b.dx,b.dy,dtSec);
      if(ips>gPeakIpsR) gPeakIpsR=ips; if(ips>gMaxIpsR) gMaxIpsR=ips; }
#endif

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

#if DEBUG_PRINT
  if(nowMs - lastIpsReportMs >= IPS_REPORT_MS){
    lastIpsReportMs = nowMs;
    Serial.print("peak ips L="); Serial.print(gPeakIpsL,1);
    Serial.print(" R=");         Serial.print(gPeakIpsR,1);
    Serial.print("  | session max L="); Serial.print(gMaxIpsL,1);
    Serial.print(" R=");                Serial.println(gMaxIpsR,1);
    gPeakIpsL = gPeakIpsR = 0.0f;
  }
#endif

  uint8_t protocolButtons=0, hidButtons=0;
  readButtons(protocolButtons, hidButtons);
  g_hidButtons = hidButtons;
  if(protocolButtons != g_protocolButtons){
    g_protocolButtons = protocolButtons;
    g_inputSequence++;
    notifyInputState();
  }

  bool pendingMotionReport = (gx!=0.0f || gy!=0.0f || gz!=0.0f);
  bool pendingHid = !g_controller && (accX!=0.0f || accY!=0.0f || accScroll!=0.0f
                                      || g_hidButtons != lastHidButtons);
  bool needHidRelease = g_controller && lastHidButtons;
  bool dueSend = Bluefruit.connected() && ((nowMs-lastSendMs) >= BLE_SEND_MS)
                 && (pendingMotionReport || pendingHid || needHidRelease);

  if(dueSend){
    lastSendMs = nowMs;
    if(!g_controller){
      if(g_hidButtons != lastHidButtons){
        bool ok = g_hidButtons ? blehid.mouseButtonPress(g_hidButtons) : blehid.mouseButtonRelease();
        if(ok) lastHidButtons = g_hidButtons;
      }
      int8_t sx=clamp8(accX), sy=clamp8(accY);
      if(sx||sy){ if(blehid.mouseMove(sx,sy)){ accX-=sx; accY-=sy; } }
      int32_t det=(int32_t)(accScroll/SCROLL_DIVISOR);
      int8_t wheel=clamp8((float)det);
      if(wheel){ if(blehid.mouseScroll(wheel)) accScroll-=(float)wheel*SCROLL_DIVISOR; }
    } else if(lastHidButtons){
      if(blehid.mouseButtonRelease()) lastHidButtons = 0;
    }

    if(pendingMotionReport){
      float rbuf[3] = { gx*ROT_SIGN_X, gy*ROT_SIGN_Y, gz*ROT_SIGN_Z };
      rotationChar.notify(rbuf, sizeof(rbuf));
      gx = gy = gz = 0.0f;
    }
  }

  // LED: disconnected = slow blink; controller = fast blink; mouse = solid.
  if(!Bluefruit.connected()){
    if(nowMs - lastBlinkMs > 600){ lastBlinkMs=nowMs; ledState=!ledState;
      digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
  } else if(g_controller){
    if(nowMs - lastBlinkMs > 200){ lastBlinkMs=nowMs; ledState=!ledState;
      digitalWrite(LED_BUILTIN, ledState?LED_STATE_ON:!LED_STATE_ON); }
  } else if(!ledState){ digitalWrite(LED_BUILTIN,LED_STATE_ON); ledState=true; }

  // Sleep the MCU when nothing is pending. Motion/button IRQs and SoftDevice wake us.
  bool idle = !g_motionWake && !g_buttonWake
           && (gx==0.0f && gy==0.0f && gz==0.0f)
           && (g_controller || (accX==0.0f && accY==0.0f && accScroll==0.0f
                                && g_hidButtons == lastHidButtons))
           && (digitalRead(PIN_MOTION_L) == HIGH)
           && (digitalRead(PIN_MOTION_R) == HIGH);
  if(idle){
    waitForEvent();
  }
}
