// -*- coding: utf-8 -*-
/*
 * ==============================================================================
 *  HSI3000U RS485 - WiFi TCP 透過ブリッジ & 自律バッテリー救済コントローラ
 *  (Lolin32 OLED 版)
 * ==============================================================================
 *  機能:
 *   - TCP ポート 8888: HSI3000U ⇔ PC (Python) 間の完全透過 Modbus RTU ブリッジ
 *   - HTTP ポート 8889: 救済設定・状態取得用 REST API (GET/POST /api/rescue)
 *   - NVS (Preferences) による救済設定・状態の不揮発性記憶
 *   - PC 接続中: 通過フレームの受動スニッフィングで電圧/状態を取得 (RS485衝突ゼロ)
 *   - PC 未接続時: ESP32 自律ポーリングで監視し、電圧低下時に CSO へ自動救済
 *   - OLED (128x64): タイトル省略・情報特化レイアウト
 * ==============================================================================
 */

#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ------------------------------------------------------------------------------
// 1. ピン定義 & ハードウェア設定
// ------------------------------------------------------------------------------
#define OLED_SDA     5
#define OLED_SCL     4
#define RS485_RX    25  // RO (Serial2 RX)
#define RS485_TX    26  // DI (Serial2 TX)

#define RS485_BAUD  9600
#define TCP_PORT    8888
#define HTTP_PORT   8889
#define SLAVE_ID    0x01

Adafruit_SSD1306 display(128, 64, &Wire, -1);

// ------------------------------------------------------------------------------
// 2. Wi-Fi & サーバー設定 (ご自身の環境に合わせて書き換えてください)
// ------------------------------------------------------------------------------
const char* ssid     = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

WiFiServer server(TCP_PORT);
WiFiClient client;
WebServer httpServer(HTTP_PORT);
Preferences prefs;

// ------------------------------------------------------------------------------
// 3. 通信統計 & テレメトリ状態
// ------------------------------------------------------------------------------
uint32_t tcpTo485Pkts   = 0;
uint32_t rs485ToTcpPkts = 0;
uint32_t lastOledUpdate = 0;
uint32_t lastWifiRetry  = 0;
uint32_t lastAutoPoll   = 0;
uint32_t lastBusActivity = 0;
bool isClientConnected  = false;
String clientRemoteIP   = "";
bool trafficBlink       = false;

// 最新テレメトリ (スニッフィングまたは自律ポーリングで更新)
float    lastBattV          = 0.0f;
uint32_t lastBattUpdateMs   = 0;
int      lastMachineState   = -1;     // 0x0210 (-1 = 未取得)
int      lastChargePriority = -1;     // 0xE20F (0:CSO, 1:CUB, 2:SNU, 3:OSO)

// PC接続中の受動スニッフィング用リクエスト追跡
uint8_t  sniffReqBuf[16];
int      sniffReqLen        = 0;
uint32_t sniffReqByteMs     = 0;
uint16_t lastReqStartReg    = 0;
uint16_t lastReqCount       = 0;
uint32_t lastReqTimeMs      = 0;

// ------------------------------------------------------------------------------
// 4. 自律救済設定 & ステートマシン
// ------------------------------------------------------------------------------
enum RescueState {
  RESCUE_IDLE = 0,
  RESCUE_ACTIVE = 1
};

enum PendingAction {
  PENDING_NONE = 0,
  PENDING_TRIGGER = 1,
  PENDING_RECOVER = 2
};

bool     rescueEnabled      = true;
float    rescueTriggerV     = 23.5f;
float    rescueRecoveryV    = 24.6f;
uint16_t rescueDebounceSec  = 10;

RescueState   rescueState   = RESCUE_IDLE;
PendingAction pendingAction = PENDING_NONE;
uint32_t      pendingSince  = 0;
String        lastActionMsg = "Standby";

// ------------------------------------------------------------------------------
// Modbus RTU CRC16 計算・検証
// ------------------------------------------------------------------------------
uint16_t calcModbusCRC(const uint8_t* buf, int len) {
  uint16_t crc = 0xFFFF;
  for (int pos = 0; pos < len; pos++) {
    crc ^= (uint16_t)buf[pos];
    for (int i = 8; i != 0; i--) {
      if ((crc & 0x0001) != 0) {
        crc >>= 1;
        crc ^= 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

bool verifyModbusCRC(const uint8_t* buf, int len) {
  if (len < 4) return false;
  uint16_t expected = calcModbusCRC(buf, len - 2);
  uint16_t actual = (uint16_t)buf[len - 2] | ((uint16_t)buf[len - 1] << 8);
  return expected == actual;
}

// ------------------------------------------------------------------------------
// NVS (Preferences) 読み書き
// ------------------------------------------------------------------------------
void loadRescuePrefs() {
  prefs.begin("rescue", true); // read-only
  rescueEnabled     = prefs.getBool("enabled", true);
  uint16_t trig10   = prefs.getUShort("trig_v10", 235);
  uint16_t rec10    = prefs.getUShort("rec_v10", 246);
  rescueDebounceSec = prefs.getUShort("deb_sec", 10);
  bool wasRescuing  = prefs.getBool("rescuing", false);
  prefs.end();

  rescueTriggerV  = trig10 / 10.0f;
  rescueRecoveryV = rec10 / 10.0f;
  if (rescueTriggerV >= rescueRecoveryV) {
    rescueRecoveryV = rescueTriggerV + 0.5f;
  }
  if (rescueDebounceSec < 1) rescueDebounceSec = 1;
  rescueState = wasRescuing ? RESCUE_ACTIVE : RESCUE_IDLE;

  Serial.printf("[NVS] Loaded: en=%d, trig=%.1fV, rec=%.1fV, deb=%us, state=%s\n",
                rescueEnabled, rescueTriggerV, rescueRecoveryV, rescueDebounceSec,
                rescueState == RESCUE_ACTIVE ? "RESCUING" : "IDLE");
}

void saveRescueConfigPrefs() {
  prefs.begin("rescue", false);
  prefs.putBool("enabled", rescueEnabled);
  prefs.putUShort("trig_v10", (uint16_t)roundf(rescueTriggerV * 10.0f));
  prefs.putUShort("rec_v10", (uint16_t)roundf(rescueRecoveryV * 10.0f));
  prefs.putUShort("deb_sec", rescueDebounceSec);
  prefs.end();
}

void saveRescueStatePrefs() {
  prefs.begin("rescue", false);
  prefs.putBool("rescuing", rescueState == RESCUE_ACTIVE);
  prefs.end();
}

// ------------------------------------------------------------------------------
// RS485 単発 Modbus RTU トランザクション (0x03 読取 / 0x06 書込)
// ------------------------------------------------------------------------------
bool modbusReadRegisters(uint16_t startReg, uint16_t count, uint16_t* outRegs) {
  uint8_t req[8];
  req[0] = SLAVE_ID;
  req[1] = 0x03;
  req[2] = (startReg >> 8) & 0xFF;
  req[3] = startReg & 0xFF;
  req[4] = (count >> 8) & 0xFF;
  req[5] = count & 0xFF;
  uint16_t crc = calcModbusCRC(req, 6);
  req[6] = crc & 0xFF;
  req[7] = (crc >> 8) & 0xFF;

  while (Serial2.available() > 0) Serial2.read();

  Serial2.write(req, 8);
  Serial2.flush();
  tcpTo485Pkts++;
  trafficBlink = true;
  lastBusActivity = millis();

  uint8_t resp[64];
  int respLen = 0;
  int expectedLen = 5 + (count * 2);
  uint32_t startWait = millis();

  while (millis() - startWait < 300) {
    while (Serial2.available() > 0) {
      if (respLen < (int)sizeof(resp)) {
        resp[respLen++] = Serial2.read();
      } else {
        Serial2.read();
      }
      startWait = millis();
      lastBusActivity = startWait;
    }
    if (respLen >= expectedLen) break;
    delay(2);
  }

  if (respLen >= expectedLen &&
      resp[0] == SLAVE_ID &&
      resp[1] == 0x03 &&
      resp[2] == (count * 2) &&
      verifyModbusCRC(resp, expectedLen)) {
    for (uint16_t i = 0; i < count; i++) {
      outRegs[i] = ((uint16_t)resp[3 + i * 2] << 8) | resp[4 + i * 2];
    }
    rs485ToTcpPkts++;
    trafficBlink = true;
    return true;
  }
  return false;
}

bool modbusWriteRegister(uint16_t reg, uint16_t value) {
  uint8_t req[8];
  req[0] = SLAVE_ID;
  req[1] = 0x06;
  req[2] = (reg >> 8) & 0xFF;
  req[3] = reg & 0xFF;
  req[4] = (value >> 8) & 0xFF;
  req[5] = value & 0xFF;
  uint16_t crc = calcModbusCRC(req, 6);
  req[6] = crc & 0xFF;
  req[7] = (crc >> 8) & 0xFF;

  while (Serial2.available() > 0) Serial2.read();

  Serial2.write(req, 8);
  Serial2.flush();
  tcpTo485Pkts++;
  trafficBlink = true;
  lastBusActivity = millis();

  uint8_t resp[16];
  int respLen = 0;
  uint32_t startWait = millis();

  while (millis() - startWait < 300) {
    while (Serial2.available() > 0) {
      if (respLen < (int)sizeof(resp)) {
        resp[respLen++] = Serial2.read();
      } else {
        Serial2.read();
      }
      startWait = millis();
      lastBusActivity = startWait;
    }
    if (respLen >= 8) break;
    delay(2);
  }

  if (respLen >= 8 &&
      resp[0] == SLAVE_ID &&
      resp[1] == 0x06 &&
      verifyModbusCRC(resp, 8)) {
    rs485ToTcpPkts++;
    trafficBlink = true;
    return true;
  }
  return false;
}

// ------------------------------------------------------------------------------
// 充電優先順位 [06] (0xE20F) の安全書込 (0xE203 ロック解除 + 書込 + 読戻検証)
// ------------------------------------------------------------------------------
bool writeChargePriority(uint16_t priorityCode) {
  Serial.printf("[Rescue] Writing charge priority -> %u\n", priorityCode);

  // 1. ロック解除 (0xE203 = 0)
  if (!modbusWriteRegister(0xE203, 0)) {
    Serial.println("[Rescue] Unlock 0xE203 failed");
    return false;
  }
  delay(80);

  // 2. 充電優先順位書込 (0xE20F = priorityCode)
  if (!modbusWriteRegister(0xE20F, priorityCode)) {
    Serial.println("[Rescue] Write 0xE20F failed");
    return false;
  }
  delay(80);

  // 3. 読戻し検証
  uint16_t verifyVal = 0xFFFF;
  if (modbusReadRegisters(0xE20F, 1, &verifyVal) && verifyVal == priorityCode) {
    lastChargePriority = (int)priorityCode;
    Serial.printf("[Rescue] Verified 0xE20F = %u\n", verifyVal);
    return true;
  }

  Serial.printf("[Rescue] Verify failed (read=%u)\n", verifyVal);
  return false;
}

// ------------------------------------------------------------------------------
// 自律ポーリング (PC未接続時、またはPC無通信時)
// ------------------------------------------------------------------------------
void runAutonomousPoll() {
  uint16_t val = 0;

  // 1. バッテリー電圧 (0x0101)
  if (modbusReadRegisters(0x0101, 1, &val)) {
    lastBattV = val * 0.1f;
    lastBattUpdateMs = millis();
  }
  delay(15);

  // 2. 動作モード (0x0210: 2=Bypass, 5=Inverter 等)
  if (modbusReadRegisters(0x0210, 1, &val)) {
    lastMachineState = (int)val;
  }
  delay(15);

  // 3. 充電優先順位 (0xE20F: 0=CSO, 1=CUB, 2=SNU, 3=OSO)
  if (modbusReadRegisters(0xE20F, 1, &val)) {
    lastChargePriority = (int)val;
  }
}

// ------------------------------------------------------------------------------
// 受動スニッフィング (PC接続中に通過フレームから値だけを抽出)
// ------------------------------------------------------------------------------
void sniffTcpTo485(const uint8_t* buf, size_t len) {
  uint32_t now = millis();
  if (now - sniffReqByteMs > 50) {
    sniffReqLen = 0;
  }
  for (size_t i = 0; i < len; i++) {
    if (sniffReqLen < (int)sizeof(sniffReqBuf)) {
      sniffReqBuf[sniffReqLen++] = buf[i];
    }
  }
  sniffReqByteMs = now;

  if (sniffReqLen == 8 && verifyModbusCRC(sniffReqBuf, 8)) {
    if (sniffReqBuf[0] == SLAVE_ID && sniffReqBuf[1] == 0x03) {
      lastReqStartReg = ((uint16_t)sniffReqBuf[2] << 8) | sniffReqBuf[3];
      lastReqCount    = ((uint16_t)sniffReqBuf[4] << 8) | sniffReqBuf[5];
      lastReqTimeMs   = now;
    }
    sniffReqLen = 0;
  }
}

void sniffRs485ToTcp(const uint8_t* buf, int len) {
  uint32_t now = millis();
  if (len < 7 || buf[0] != SLAVE_ID) return;

  // 0x03 読取応答の解析
  if (buf[1] == 0x03 && (now - lastReqTimeMs < 1000)) {
    uint8_t byteCount = buf[2];
    if (byteCount == lastReqCount * 2 && len == 5 + byteCount && verifyModbusCRC(buf, len)) {
      for (uint16_t i = 0; i < lastReqCount; i++) {
        uint16_t regAddr = lastReqStartReg + i;
        uint16_t regVal  = ((uint16_t)buf[3 + i * 2] << 8) | buf[4 + i * 2];
        if (regAddr == 0x0101) {
          lastBattV = regVal * 0.1f;
          lastBattUpdateMs = now;
        } else if (regAddr == 0x0210) {
          lastMachineState = (int)regVal;
        } else if (regAddr == 0xE20F) {
          lastChargePriority = (int)regVal;
        }
      }
    }
  }
  // 0x06 単一書込エコー応答の解析 (PC手動変更の即時反映)
  else if (buf[1] == 0x06 && len == 8 && verifyModbusCRC(buf, 8)) {
    uint16_t regAddr = ((uint16_t)buf[2] << 8) | buf[3];
    uint16_t regVal  = ((uint16_t)buf[4] << 8) | buf[5];
    if (regAddr == 0xE20F) {
      lastChargePriority = (int)regVal;
    }
  }
}

// ------------------------------------------------------------------------------
// 自律救済ロジック評価
// ------------------------------------------------------------------------------
void evaluateRescueLogic() {
  uint32_t now = millis();

  // 電圧が古い (6秒以上更新なし) または異常値 (<= 5.0V) なら判定保留
  if (lastBattUpdateMs == 0 || (now - lastBattUpdateMs > 6000) || lastBattV <= 5.0f) {
    return;
  }

  // 外部で OSO (3) に戻されていた場合は IDLE へ同期
  if (rescueState == RESCUE_ACTIVE && lastChargePriority == 3) {
    rescueState = RESCUE_IDLE;
    pendingAction = PENDING_NONE;
    saveRescueStatePrefs();
    lastActionMsg = "Synced IDLE (OSO detected)";
    Serial.println("[Rescue] External OSO detected -> IDLE");
  }

  if (!rescueEnabled) {
    pendingAction = PENDING_NONE;
    return;
  }

  // PC 接続中はバス無通信タイミング (直近通信から 40ms 以上経過) を待つ
  auto canAccessBus = [&]() -> bool {
    if (!isClientConnected) return true;
    return (now - lastBusActivity >= 40) && (sniffReqLen == 0);
  };

  if (rescueState == RESCUE_IDLE) {
    if (lastBattV <= rescueTriggerV) {
      if (pendingAction != PENDING_TRIGGER) {
        pendingAction = PENDING_TRIGGER;
        pendingSince = now;
      } else if (now - pendingSince >= (uint32_t)rescueDebounceSec * 1000UL) {
        if (canAccessBus()) {
          if (writeChargePriority(0)) { // 0 = CSO
            rescueState = RESCUE_ACTIVE;
            pendingAction = PENDING_NONE;
            saveRescueStatePrefs();
            lastActionMsg = "Rescue START -> CSO (" + String(lastBattV, 1) + "V)";
          }
        }
      }
    } else {
      pendingAction = PENDING_NONE;
    }
  } else { // RESCUE_ACTIVE
    if (lastBattV >= rescueRecoveryV) {
      if (pendingAction != PENDING_RECOVER) {
        pendingAction = PENDING_RECOVER;
        pendingSince = now;
      } else if (now - pendingSince >= (uint32_t)rescueDebounceSec * 1000UL) {
        if (canAccessBus()) {
          if (writeChargePriority(3)) { // 3 = OSO
            rescueState = RESCUE_IDLE;
            pendingAction = PENDING_NONE;
            saveRescueStatePrefs();
            lastActionMsg = "Rescue END -> OSO (" + String(lastBattV, 1) + "V)";
          }
        }
      }
    } else {
      pendingAction = PENDING_NONE;
    }
  }
}

// ------------------------------------------------------------------------------
// HTTP REST API (Port 8889)
// ------------------------------------------------------------------------------
String buildRescueStatusJson() {
  uint32_t now = millis();
  float pendSec = 0.0f;
  const char* pendStr = "null";
  if (pendingAction == PENDING_TRIGGER) {
    pendStr = "\"TRIGGER\"";
    pendSec = (now - pendingSince) / 1000.0f;
  } else if (pendingAction == PENDING_RECOVER) {
    pendStr = "\"RECOVER\"";
    pendSec = (now - pendingSince) / 1000.0f;
  }

  String json = "{";
  json += "\"enabled\":" + String(rescueEnabled ? "true" : "false") + ",";
  json += "\"trigger_voltage\":" + String(rescueTriggerV, 1) + ",";
  json += "\"recovery_voltage\":" + String(rescueRecoveryV, 1) + ",";
  json += "\"debounce_sec\":" + String(rescueDebounceSec) + ",";
  json += "\"state\":\"" + String(rescueState == RESCUE_ACTIVE ? "RESCUING" : "IDLE") + "\",";
  json += "\"pending_action\":" + String(pendStr) + ",";
  json += "\"pending_seconds\":" + String(pendSec, 1) + ",";
  json += "\"battery_voltage\":" + String(lastBattV, 1) + ",";
  json += "\"machine_state\":" + String(lastMachineState) + ",";
  json += "\"charge_priority_code\":" + String(lastChargePriority) + ",";
  json += "\"last_action_msg\":\"" + lastActionMsg + "\",";
  json += "\"uptime_sec\":" + String(now / 1000UL);
  json += "}";
  return json;
}

// 簡易 JSON キー値抽出ヘルパー (外部ライブラリ不要)
bool extractJsonValue(const String& body, const char* key, String& outVal) {
  String pattern = "\"" + String(key) + "\"";
  int idx = body.indexOf(pattern);
  if (idx < 0) return false;
  int colon = body.indexOf(':', idx + pattern.length());
  if (colon < 0) return false;
  int start = colon + 1;
  while (start < (int)body.length() && (body[start] == ' ' || body[start] == '\t' || body[start] == '\"')) {
    start++;
  }
  int end = start;
  while (end < (int)body.length() && body[end] != ',' && body[end] != '}' && body[end] != '\"' && body[end] != ' ') {
    end++;
  }
  if (end <= start) return false;
  outVal = body.substring(start, end);
  outVal.trim();
  return true;
}

void handleHttpGetRescue() {
  httpServer.send(200, "application/json", buildRescueStatusJson());
}

void handleHttpPostRescue() {
  String body = httpServer.arg("plain");
  String val;

  if (extractJsonValue(body, "enabled", val)) {
    rescueEnabled = (val == "true" || val == "1");
  }
  if (extractJsonValue(body, "trigger_voltage", val)) {
    float tv = val.toFloat();
    if (tv >= 10.0f && tv <= 60.0f) {
      rescueTriggerV = roundf(tv * 10.0f) / 10.0f;
    }
  }
  if (extractJsonValue(body, "recovery_voltage", val)) {
    float rv = val.toFloat();
    if (rv >= 10.0f && rv <= 60.0f) {
      rescueRecoveryV = roundf(rv * 10.0f) / 10.0f;
    }
  }
  if (extractJsonValue(body, "debounce_sec", val)) {
    int ds = val.toInt();
    if (ds >= 1 && ds <= 600) {
      rescueDebounceSec = (uint16_t)ds;
    }
  }

  if (rescueTriggerV >= rescueRecoveryV) {
    rescueRecoveryV = roundf((rescueTriggerV + 0.5f) * 10.0f) / 10.0f;
  }

  saveRescueConfigPrefs();
  Serial.printf("[HTTP] Config updated: en=%d, trig=%.1fV, rec=%.1fV, deb=%us\n",
                rescueEnabled, rescueTriggerV, rescueRecoveryV, rescueDebounceSec);

  httpServer.send(200, "application/json", buildRescueStatusJson());
}

// ------------------------------------------------------------------------------
// OLED 画面描画 (タイトル省略・情報特化レイアウト)
// ------------------------------------------------------------------------------
const char* getModeLabel(int mState) {
  switch (mState) {
    case 0:  return "PWR: INIT DELAY";
    case 1:  return "PWR: WAITING";
    case 2:  return "PWR: BYPASS (AC)";
    case 3:  return "PWR: SOFT START";
    case 4:  return "PWR: STANDBY";
    case 5:  return "PWR: INVERTER";
    case 6:  return "PWR: SW -> BYPASS";
    case 7:  return "PWR: SW -> INV";
    case 8:  return "PWR: BATT ACT";
    case 9:  return "PWR: SHUTDOWN";
    case 10: return "PWR: OFF";
    case 11: return "PWR: FAULT!";
    default: return "PWR: ---";
  }
}

const char* getChargeLabel(int prio) {
  switch (prio) {
    case 0:  return "CSO";
    case 1:  return "CUB";
    case 2:  return "SNU";
    case 3:  return "OSO";
    default: return "---";
  }
}

void updateOled() {
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // 1行目 (y=0): 接続状態 / IPアドレス + 通信アクティビティ '*'
  display.setTextSize(1);
  display.setCursor(0, 0);
  if (WiFi.status() == WL_CONNECTED) {
    display.print(WiFi.localIP().toString());
    if (isClientConnected && client && client.connected()) {
      display.print(" [PC]");
    }
  } else {
    display.print("WiFi Connecting...");
  }
  if (trafficBlink) {
    display.setCursor(122, 0);
    display.print("*");
    trafficBlink = false;
  }
  display.drawLine(0, 10, 127, 10, SSD1306_WHITE);

  // 2行目 (y=13): バッテリー電圧 (大文字) + 右側にPC/WiFi状態
  display.setTextSize(2);
  display.setCursor(0, 13);
  if (lastBattUpdateMs > 0 && lastBattV > 1.0f) {
    display.printf("%.1fV", lastBattV);
  } else {
    display.print("--.-V");
  }

  display.setTextSize(1);
  display.setCursor(74, 13);
  display.print(isClientConnected ? "PC:LINK" : "PC:WAIT");
  display.setCursor(74, 22);
  if (WiFi.status() == WL_CONNECTED) {
    display.printf("%ddBm", WiFi.RSSI());
  } else {
    display.print("NO WIFI");
  }

  // 3行目 (y=33): インバータ給電 / パスルー (バイパス) 表示
  display.setCursor(0, 33);
  display.print(getModeLabel(lastMachineState));

  // 4行目 (y=44): 充電モード & レスキューモード状態
  display.setCursor(0, 44);
  const char* rscStr = !rescueEnabled ? "OFF" : (rescueState == RESCUE_ACTIVE ? "ACTIVE!" : "WAIT");
  display.printf("CHG:%s  RSC:%s", getChargeLabel(lastChargePriority), rscStr);

  // 5行目 (y=55): 救済閾値 または デバウンスカウントダウン
  display.setCursor(0, 55);
  if (pendingAction != PENDING_NONE) {
    uint32_t elapsed = (millis() - pendingSince) / 1000UL;
    const char* act = (pendingAction == PENDING_TRIGGER) ? "->CSO" : "->OSO";
    display.printf("RESCUE %s (%lu/%us)", act, elapsed, rescueDebounceSec);
  } else {
    display.printf("%.1f->CSO  %.1f->OSO", rescueTriggerV, rescueRecoveryV);
  }

  display.display();
}

// ------------------------------------------------------------------------------
// 初期設定 (setup)
// ------------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n=== HSI3000U WiFi Bridge + AutoRescue Starting ===");

  loadRescuePrefs();

  Wire.begin(OLED_SDA, OLED_SCL);
  Wire.setClock(400000);

  if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    updateOled();
  }

  Serial2.begin(RS485_BAUD, SERIAL_8N1, RS485_RX, RS485_TX);

  // 初回RS485ポーリング
  runAutonomousPoll();

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);

  uint32_t startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[WiFi] IP: %s\n", WiFi.localIP().toString().c_str());
  }

  // TCP 透過サーバー (Port 8888)
  server.begin();
  server.setNoDelay(true);

  // HTTP 管理 API サーバー (Port 8889)
  httpServer.on("/api/rescue", HTTP_GET, handleHttpGetRescue);
  httpServer.on("/api/rescue", HTTP_POST, handleHttpPostRescue);
  httpServer.begin();
  Serial.printf("[Server] TCP:%d, HTTP:%d ready\n", TCP_PORT, HTTP_PORT);

  updateOled();
}

// ------------------------------------------------------------------------------
// メインループ (loop)
// ------------------------------------------------------------------------------
void loop() {
  uint32_t now = millis();

  // 1. Wi-Fi 再接続監視
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiRetry >= 5000) {
      lastWifiRetry = now;
      WiFi.reconnect();
    }
  }

  // 2. HTTP 管理 API リクエスト処理
  httpServer.handleClient();

  // 3. 新規 TCP クライアント接続処理 (Port 8888)
  if (server.hasClient()) {
    WiFiClient newClient = server.available();
    if (newClient) {
      if (!client || !client.connected()) {
        if (client) client.stop();
        client = newClient;
        client.setNoDelay(true);
        isClientConnected = true;
        clientRemoteIP = client.remoteIP().toString();
        sniffReqLen = 0;
        Serial.printf("[TCP] Client Connected: %s\n", clientRemoteIP.c_str());
        updateOled();
      } else {
        newClient.stop();
      }
    }
  }

  if (isClientConnected && (!client || !client.connected())) {
    client.stop();
    isClientConnected = false;
    clientRemoteIP = "";
    sniffReqLen = 0;
    Serial.println("[TCP] Client Disconnected");
    updateOled();
  }

  // 4. 自律ポーリング (PC未接続時、またはPC接続中だが5秒以上通信がない場合)
  bool needAutoPoll = !isClientConnected ||
                      ((now - lastBusActivity > 5000) && (now - lastBattUpdateMs > 5000));
  if (needAutoPoll && (now - lastAutoPoll >= 2000)) {
    lastAutoPoll = now;
    runAutonomousPoll();
  }

  // 5. データ中継: TCP (Python) -> RS485 (HSI3000U) + 受動スニッフィング
  if (client && client.connected() && client.available() > 0) {
    size_t avail = client.available();
    if (avail > 256) avail = 256;
    uint8_t tcpBuf[256];
    size_t bytesRead = client.readBytes(tcpBuf, avail);
    if (bytesRead > 0) {
      sniffTcpTo485(tcpBuf, bytesRead);
      Serial2.write(tcpBuf, bytesRead);
      Serial2.flush();
      tcpTo485Pkts++;
      trafficBlink = true;
      lastBusActivity = millis();
    }
  }

  // 6. データ中継: RS485 (HSI3000U) -> TCP (Python) + 受動スニッフィング
  if (Serial2.available() > 0) {
    uint8_t rs485Buf[256];
    int len = 0;
    uint32_t lastCharTime = millis();

    while (millis() - lastCharTime < 4) {
      while (Serial2.available() > 0) {
        if (len < (int)sizeof(rs485Buf)) {
          rs485Buf[len++] = Serial2.read();
        } else {
          Serial2.read();
        }
        lastCharTime = millis();
      }
    }

    if (len > 0) {
      lastBusActivity = millis();
      sniffRs485ToTcp(rs485Buf, len);
      if (client && client.connected()) {
        client.write(rs485Buf, len);
        client.flush();
        rs485ToTcpPkts++;
        trafficBlink = true;
      }
    }
  }

  // 7. 自律救済ロジック評価 (必要時は安全にバス調停して切替実行)
  evaluateRescueLogic();

  // 8. OLED 定期更新 (500ms 周期)
  if (now - lastOledUpdate >= 500) {
    lastOledUpdate = now;
    updateOled();
  }
}
