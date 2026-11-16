# HSImonitor 構築・運用ガイド

[English (guide.md)](guide.md) | **日本語**

---

## 1. 全体構成

本システムは、LVYUAN **HSI3000U**（SRNE OEM 互換）インバータの RS485 (Modbus RTU) ポートに **ESP32 (Lolin32 OLED)** を接続し、以下の 2 つの役割を同時に実現します。

* **Wi-Fi ⇔ RS485 完全透過ブリッジ（TCP Port `8888`）**：PC の Python 監視サーバーから無線でインバータをリアルタイム監視・設定変更。
* **ESP32 自律バッテリー救済コントローラ（HTTP API Port `8889`）**：PC が停止・再起動中であっても、ESP32 単独でバッテリー電圧低下を検知し、充電優先順位 `[06]` を `OSO`（太陽光のみ）から `CSO`（商用緊急充電）へ自動切替・回復復帰を実行。

```text
[HSI3000U インバータ]
       |  RS485 (9600bps / Modbus RTU / Slave ID=1)
       v
[ESP32 (Lolin32 OLED) + RS485モジュール]
  |-- 自律救済エンジン & NVS(Preferences)  -> PC不在時は2秒周期で監視・緊急充電切替
  |-- TCP Port 8888 (完全透過ブリッジ)    -> PC接続時は通過パケットを受動スニッフィング(衝突ゼロ)
  +-- HTTP Port 8889 (/api/rescue)        -> 救済設定の読み書き・状態同期用 REST API
       |  Wi-Fi (LAN)
       v
[PC 監視サーバー (server.py / FastAPI)]
  +-- HTTP / WebSocket (Port 8000)
       v
[ブラウザ / スマートフォン Web UI]
```

> **RS485 バス調停：** PC 接続中は、ESP32 が通過する Modbus 応答（`0x0101`, `0x0210`, `0xE20F`）を受動スニッフィング（盗聴）して値を更新するため、RS485 バス上で通信が衝突しません。救済切替の書き込み時のみ、バス無通信区間を待って `0xE203` ロック解除＋`0xE20F` 書込を実行します。

---

## 2. ハードウェア作成

### 必要な部品

* **マイコンボード**：ESP32 Lolin32 OLED（0.96インチ SSD1306 I2C OLED 内蔵）
* **RS485 変換モジュール**：自動送受信切替タイプ（3.3V 対応、TX/RX と A/B 端子のみの 2 線式推奨）
* **LAN ケーブル（RJ45）**：HSI3000U の RS485 通信ポート接続用
* **電源**：USB 5V 電源

### ESP32 ⇔ RS485 モジュール結線

| ESP32 (Lolin32 OLED) | 接続先 | 備考 |
| :--- | :--- | :--- |
| `GPIO 25` (Serial2 RX) | RS485 モジュール `RO / RXD` | `HSI485conv.ino` の `RS485_RX` |
| `GPIO 26` (Serial2 TX) | RS485 モジュール `DI / TXD` | `HSI485conv.ino` の `RS485_TX` |
| `3.3V` / `GND` | RS485 モジュール `VCC` / `GND` | 3.3V 動作を必ず確認 |
| `GPIO 5` / `GPIO 4` | 内蔵 OLED `SDA` / `SCL` | 基板内部で配線済み（I2C `0x3C`） |

### HSI3000U (RJ45) ⇔ RS485 結線

インバータ本体の通信ポート（RJ45）から **RS485 A(+) / B(-)** を取り出し、RS485 モジュールの `A`・`B` に接続します。応答がない場合は `A` と `B` を入れ替えて確認してください。

> **注意：** USB-RS485 アダプタを使って PC とインバータを直接繋ぐ場合は、ESP32 側の RS485 配線（または電源）を外してください。同一バス上に PC と ESP32 が別々にマスターとして繋がると通信が衝突します。

---

## 3. ESP書き込みとESP単体での動作チェック

### Arduino IDE での設定と書き込み

1. Arduino IDE で `HSI485conv.ino` を開きます。
2. ライブラリマネージャから以下をインストールします（`WiFi` / `WebServer` / `Preferences` は ESP32 コア標準付属）：
   * `Adafruit GFX Library`
   * `Adafruit SSD1306`
3. コード先頭付近の Wi-Fi 設定をご自身の環境に合わせて書き換えます：
   ```cpp
   const char* ssid     = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```
4. ボード設定で **WEMOS LOLIN32**（または **ESP32 Dev Module**）を選択し、USB 接続して書き込みます。

### OLED 画面の見方（PC なし単体動作時）

電源投入後、PC サーバーを起動しなくても ESP32 単体でインバータと通信し、以下の 5 行が表示されます。

```text
192.168.1.85              *
25.4V              PC:WAIT
                   -58dBm
PWR: INVERTER
CHG:OSO  RSC:WAIT
23.5->CSO  24.6->OSO
```

1. **1行目**：Wi-Fi 接続中は `WiFi Connecting...`、接続後は IP アドレス（PC 接続時は `[PC]` 付記）と通信点滅 `*`。
2. **2行目**：現在のバッテリー電圧（大文字）、PC 接続状態（`PC:WAIT` / `PC:LINK`）、Wi-Fi 電波強度。
3. **3行目**：`PWR: INVERTER`（インバータ給電） / `PWR: BYPASS (AC)`（商用パスルー）等の運転状態。
4. **4行目**：充電優先順位（`OSO`/`CSO`/`CUB`/`SNU`）と救済状態（`WAIT` / `ACTIVE!` / `OFF`）。
5. **5行目**：救済開始・終了電圧、または切替判定中のカウントダウン（例：`RESCUE ->CSO (4/10s)`）。

### HTTP API による単体動作チェック

PC やスマホのブラウザ、または `curl` で以下の URL にアクセスし、JSON が返れば ESP32 単体での動作は正常です。

```bash
curl http://<ESP32のIPアドレス>:8889/api/rescue
```

> 起動時に Wi-Fi に繋がらない環境でも、15 秒でタイムアウトして通常ループへ進み、5 秒ごとに裏で Wi-Fi 再接続を試みながら RS485 監視と自律バッテリー救済を継続します。

---

## 4. PCサーバーからのアクセス

### セットアップと起動（Windows）

1. Python 3.10〜3.12 をインストールします。
2. `setup_requirements.bat` を実行（または `pip install -r requirements.txt`）して依存パッケージをインストールします。
3. 必要に応じて `server.py` 内の接続先 IP（初期値 `socket://192.168.1.85:8888`）を ESP32 の OLED に表示された IP に書き換えます。
4. `run.bat`（または `python -u server.py`）で起動し、`stop.bat` で停止します。

### Wi-Fi 接続と USB-RS485 直結の自動切替

* **`socket://...` 接続時**：**ESP32 自律監視モード**（PC は Port `8889` から状態同期と設定反映のみ実施）。
* **`COMx`（USB-RS485 直結）時**：**PC 監視モード**（自動的に Python 側のローカル救済制御へフォールバック）。

---

## 5. WEBでの操作

サーバー起動後、PC のブラウザで `http://localhost:8000` を開きます。同一 Wi-Fi 内のスマートフォンやタブレットからは `http://<PCのIPアドレス>:8000` で閲覧・操作できます。

* **リアルタイム電力フロー & 24時間推移グラフ**：太陽光(PV)・バッテリー・商用(Grid)・AC 負荷の流れを可視化します。
* **夜間バッテリー保護・自動救済充電パネル**：自動救済の `ON/OFF`、開始電圧、停止電圧を設定して保存すると、ESP32 側の NVS に即座に書き込まれます。
* **充電優先順位の手動切替**：`CSO`・`CUB`・`SNU`・`OSO` を手動変更できます。手動で `OSO` に戻すと ESP32 側の救済状態も自動で `IDLE` に同期します。
* **バッテリー詳細 / 履歴 / 障害診断**：LiFePO4 8S 適応型 SOC 推定、1分間隔・日別集計グラフ、全64種のエラーコード事典、CSV ログダウンロードが利用できます。

