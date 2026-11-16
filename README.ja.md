# HSImonitor (LVYUAN HSI3000U インバータ監視 ＆ ESP32 自律バッテリー救済)

[English (README.md)](README.md) | **日本語**

LVYUAN ハイブリッドインバータ **HSI3000U**（SRNE OEM 互換）向けの、**ESP32 Wi-Fi RS485 ブリッジ＋自律バッテリー救済コントローラ** と **PC 監視 Web サーバー** の統合システムです。

## 主な特徴

1. **ESP32 単体での自律バッテリー救済（PC 停止・再起動中も保護）**
   * ESP32 がインバータのバッテリー電圧を常時監視し、設定下限（例: `23.5V`）を下回ると充電優先順位 `[06]` を `OSO`（太陽光のみ）から `CSO`（商用緊急充電）へ自動切替。
   * 復帰電圧（例: `24.6V`）まで回復すると自動で `OSO` に戻します。
   * 設定値と救済状態は ESP32 内蔵フラッシュ（NVS / `Preferences`）に不揮発保存されます。
2. **完全透過 Modbus RTU ブリッジ（Port `8888`）＋ 管理 HTTP API（Port `8889`）**
   * Port `8888` は純粋な TCP ⇔ RS485 透過ブリッジを維持しつつ、通過パケットの受動スニッフィングにより RS485 バスの衝突を回避。
   * Port `8889` の REST API（`GET/POST /api/rescue`）で PC やブラウザから救済状態の確認・設定変更が可能。
3. **PC 監視 Web ダッシュボード（FastAPI + WebSocket, Port `8000`）**
   * リアルタイム電力フロー、24時間推移グラフ、LiFePO4 8S 適応型 SOC 推定、64種の障害診断事典、CSV ロギングを搭載。
   * USB-RS485 アダプタ直結（`COMx`）時は自動で PC 監視モードにフォールバックします。

## ドキュメント

* **構築・運用ガイド（Markdown）：** [docs/guide.ja.md](docs/guide.ja.md) | [English (docs/guide.md)](docs/guide.md)
* **HTML マニュアル：** [docs/index.html](docs/index.html)（ブラウザ閲覧・GitHub Pages 対応）

## クイックスタート

1. `HSI485conv.ino` の `ssid` / `password` をご自身の Wi-Fi 環境に合わせて書き換え、ESP32（Lolin32 OLED）へ書き込みます。
2. `setup_requirements.bat`（または `pip install -r requirements.txt`）で Python 依存パッケージをインストールします。
3. `server.py` の接続先 IP（初期値 `socket://192.168.1.85:8888`）を必要に応じて変更し、`run.bat`（または `python -u server.py`）で起動します。
4. ブラウザで `http://localhost:8000` を開きます。

