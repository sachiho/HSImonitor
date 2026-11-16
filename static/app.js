/* ==========================================================================
   LVYUAN HSI3000U Solar Inverter Pro Monitor - Application Logic
   Includes Live Terminal, Fault Diagnostic Center, and Full Error Encyclopedia
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  const tr = (key, params, fallback) => (window.I18N ? window.I18N.t(key, params, fallback) : (fallback ?? key));
  let lastTelemetryData = null;

  // Navigation & Elements
  const langSelect = document.getElementById("langSelect");
  if (window.I18N) {
    window.I18N.ready.then((lang) => {
      if (langSelect) langSelect.value = lang;
      window.I18N.applyI18n(document);
      updateChartsLocale();
      if (lastTelemetryData) updateDashboard(lastTelemetryData);
      if (faultDatabaseCache && Object.keys(faultDatabaseCache).length > 0) {
        renderFaultEncyclopedia(faultDatabaseCache);
      }
    });
    if (langSelect) {
      langSelect.addEventListener("change", (e) => window.I18N.setLang(e.target.value));
    }
    window.I18N.onLangChange(() => {
      settingsRendered = false;
      updateChartsLocale();
      updateChartRangeBadge();
      if (lastTelemetryData) updateDashboard(lastTelemetryData);
      if (faultDatabaseCache && Object.keys(faultDatabaseCache).length > 0) {
        renderFaultEncyclopedia(faultDatabaseCache);
      }
      if (dailySummaryRecords && dailySummaryRecords.length > 0) {
        refreshHistoryList();
      }
    });
  }

  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const portSelect = document.getElementById("portSelect");
  const btnRefreshPorts = document.getElementById("btnRefreshPorts");
  const btnConnect = document.getElementById("btnConnect");
  const btnSimulate = document.getElementById("btnSimulate");
  const btnExportCsv = document.getElementById("btnExportCsv");
  const btnLanHelp = document.getElementById("btnLanHelp");
  const lanModal = document.getElementById("lanModal");
  const btnCloseModal = document.getElementById("btnCloseModal");
  const lanAccessUrl = document.getElementById("lanAccessUrl");
  const footerTimestamp = document.getElementById("footerTimestamp");
  const footerConnectionInfo = document.getElementById("footerConnectionInfo");

  // Fault Alert elements
  const topFaultBanner = document.getElementById("topFaultBanner");
  const topFaultTitle = document.getElementById("topFaultTitle");
  const topFaultDesc = document.getElementById("topFaultDesc");
  const btnGoToFaults = document.getElementById("btnGoToFaults");
  const navFaultBadge = document.getElementById("navFaultBadge");
  const activeFaultsList = document.getElementById("activeFaultsList");
  const activeFaultCountBadge = document.getElementById("activeFaultCountBadge");
  const faultHistoryBody = document.getElementById("faultHistoryBody");
  const btnClearFaultHistory = document.getElementById("btnClearFaultHistory");
  const faultSearchInput = document.getElementById("faultSearchInput");
  const faultEncyclopediaBody = document.getElementById("faultEncyclopediaBody");

  // Live Terminal elements
  const terminalWindow = document.getElementById("terminalWindow");
  const btnClearTerminal = document.getElementById("btnClearTerminal");
  const chkAutoScroll = document.getElementById("chkAutoScroll");

  // Flow SVG elements
  const lineSolarToInv = document.getElementById("lineSolarToInv");
  const lineInvToBatt = document.getElementById("lineInvToBatt");
  const lineGridToInv = document.getElementById("lineGridToInv");
  const lineInvToLoad = document.getElementById("lineInvToLoad");
  const lineBypassToLoad = document.getElementById("lineBypassToLoad");
  const tagInvPath = document.getElementById("tagInvPath");
  const tagBypassPath = document.getElementById("tagBypassPath");
  const flowNodeInv = document.getElementById("flowNodeInv");
  const bypassRelayGroup = document.getElementById("bypassRelayGroup");
  const bypassSwitchText = document.getElementById("bypassSwitchText");
  const flowStateBadgeBg = document.getElementById("flowStateBadgeBg");
  const flowStateBadgeText = document.getElementById("flowStateBadgeText");
  const flowModePill = document.getElementById("flowModePill");
  const flowModeText = document.getElementById("flowModeText");

  // --- Chart Setup & 24H History State ---
  let powerChart = null;
  let dayDetailChart = null;
  let dailyBarChart = null;

  let rawHistory24h = [];       // Array of 1-minute averaged points from /api/history/24h
  let liveHistory = [];         // Rolling 60 points for live real-time mode
  let currentChartRange = "24h"; // "24h", "12h", "6h", "1h", "live"
  let dailySummaryRecords = []; // Array of daily records from /api/history/days
  let currentSelectedDate = "";

  initPowerChart();
  initHistoryTab();
  load24hHistory();

  // Navigation tab switching & Select combobox handling
  const panes = document.querySelectorAll(".tab-pane");
  const selectMetrics = document.getElementById("selectMetrics");
  const selectTools = document.getElementById("selectTools");

  const metricsTabs = ["tab-battery", "tab-solar", "tab-inverter", "tab-grid", "tab-hardware"];
  const toolsTabs = ["tab-scanner", "tab-terminal"];

  function switchTab(targetId) {
    if (!targetId) return;

    // Remove active from all nav tab buttons
    document.querySelectorAll(".nav-tab").forEach(t => t.classList.remove("active"));
    panes.forEach(p => p.classList.remove("active"));

    // Activate direct button if matching data-tab
    const targetBtn = document.querySelector(`.nav-tab[data-tab="${targetId}"]`);
    if (targetBtn) {
      targetBtn.classList.add("active");
    }

    // Handle Metrics combobox state
    if (metricsTabs.includes(targetId)) {
      if (selectMetrics) {
        selectMetrics.value = targetId;
        selectMetrics.classList.add("active");
      }
    } else {
      if (selectMetrics) {
        selectMetrics.value = "";
        selectMetrics.classList.remove("active");
      }
    }

    // Handle Tools combobox state
    if (toolsTabs.includes(targetId)) {
      if (selectTools) {
        selectTools.value = targetId;
        selectTools.classList.add("active");
      }
    } else {
      if (selectTools) {
        selectTools.value = "";
        selectTools.classList.remove("active");
      }
    }

    // Activate target pane
    const targetPane = document.getElementById(targetId);
    if (targetPane) {
      targetPane.classList.add("active");
    }

    if (targetId === "tab-history") {
      loadDailyHistory();
    }
  }

  // Hook all buttons with data-tab
  document.querySelectorAll(".nav-tab[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      if (tabId) switchTab(tabId);
    });
  });

  // Hook combobox changes
  if (selectMetrics) {
    selectMetrics.addEventListener("change", (e) => {
      if (e.target.value) {
        switchTab(e.target.value);
      }
    });
  }

  if (selectTools) {
    selectTools.addEventListener("change", (e) => {
      if (e.target.value) {
        switchTab(e.target.value);
      }
    });
  }

  // Clickable stat cards on overview screen that jump to detailed tabs
  document.querySelectorAll(".clickable-card").forEach(card => {
    card.addEventListener("click", () => {
      const targetTab = card.getAttribute("data-goto");
      if (targetTab) switchTab(targetTab);
    });
  });

  if (btnGoToFaults) {
    btnGoToFaults.addEventListener("click", () => switchTab("tab-faults"));
  }

  // Scanner presets
  document.querySelectorAll(".btn-preset").forEach(btn => {
    btn.addEventListener("click", () => {
      document.getElementById("scanStartAddr").value = btn.getAttribute("data-addr");
      document.getElementById("scanCount").value = btn.getAttribute("data-count");
      runRegisterScan();
    });
  });

  document.getElementById("btnRunScan").addEventListener("click", runRegisterScan);

  // Terminal clear
  if (btnClearTerminal) {
    btnClearTerminal.addEventListener("click", () => {
      terminalWindow.innerHTML = "";
    });
  }

  // Modal handlers
  btnLanHelp.addEventListener("click", () => {
    lanAccessUrl.textContent = `http://${window.location.hostname}:8000`;
    lanModal.classList.remove("hidden");
  });
  btnCloseModal.addEventListener("click", () => lanModal.classList.add("hidden"));
  lanModal.addEventListener("click", (e) => {
    if (e.target === lanModal) lanModal.classList.add("hidden");
  });

  // Connection handlers
  btnRefreshPorts.addEventListener("click", fetchPorts);
  btnConnect.addEventListener("click", () => {
    const selectedPort = portSelect.value || "COM5";
    connectInverter(selectedPort, false);
  });
  btnSimulate.addEventListener("click", () => {
    connectInverter("COM5", true);
  });
  btnExportCsv.addEventListener("click", () => {
    window.location.href = "/api/log/export";
  });

  // Load Fault Encyclopedia
  let faultDatabaseCache = {};
  loadFaultEncyclopedia();
  initAutoRescuePanel();

  if (faultSearchInput) {
    faultSearchInput.addEventListener("input", filterFaultEncyclopedia);
  }

  // Start WebSocket connection
  initWebSocket();
  fetchPorts();

  // -------------------------------------------------------------------------
  // -------------------------------------------------------------------------
  // WebSocket Data Receiver & Live Heartbeat Monitoring
  // -------------------------------------------------------------------------
  let lastDataReceivedTime = 0;
  const heartbeatDot = document.getElementById("heartbeatDot");
  const lastUpdatedSec = document.getElementById("lastUpdatedSec");

  function triggerHeartbeatPulse() {
    lastDataReceivedTime = Date.now();
    const nowStr = new Date().toLocaleTimeString();
    if (lastUpdatedSec) lastUpdatedSec.textContent = nowStr;
    if (heartbeatDot) {
      heartbeatDot.classList.add("flash");
      setTimeout(() => heartbeatDot.classList.remove("flash"), 200);
    }
  }

  function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);

    // Keepalive ping every 10 seconds
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send("ping"); } catch (e) {}
      }
    }, 10000);

    ws.onopen = () => {
      addTerminalLog("SUCCESS", "WebSocket接続完了: インバータテレメトリ監視を開始しました");
    };

    ws.onmessage = (event) => {
      try {
        if (event.data === "pong") return;
        const data = JSON.parse(event.data);
        triggerHeartbeatPulse();
        updateDashboard(data);
      } catch (err) {
        console.error("Error parsing telemetry frame:", err);
      }
    };

    ws.onclose = () => {
      clearInterval(pingInterval);
      statusBadge.className = "status-indicator";
      statusText.textContent = tr("header.status_reconnecting", null, "サーバー再接続中...");
      addTerminalLog("WARN", "WebSocket disconnected. Retrying in 2s...");
      setTimeout(initWebSocket, 2000);
    };

    ws.onerror = (err) => {
      console.warn("WebSocket error:", err);
      ws.close();
    };
  }

  // Automatic HTTP Polling Fallback (ensures screen updates even if WebSocket drops)
  setInterval(async () => {
    if (Date.now() - lastDataReceivedTime > 2500) {
      try {
        const res = await fetch("/api/status");
        if (res.ok) {
          const data = await res.json();
          triggerHeartbeatPulse();
          updateDashboard(data);
        }
      } catch (e) {
        // Offline or connection error
      }
    }
  }, 2000);

  // -------------------------------------------------------------------------
  // Dashboard UI Updates
  // -------------------------------------------------------------------------
  function updateDashboard(data) {
    if (!data) return;
    lastTelemetryData = data;

    const isConnected = data.connected;
    const isSimulated = data.simulated;

    // Update Status Badge
    if (isConnected) {
      if (isSimulated) {
        statusBadge.className = "status-indicator demo";
        statusText.textContent = tr("header.status_demo", null, "デモシミュレーション中");
        footerConnectionInfo.textContent = "DEMO SIMULATION";
      } else {
        statusBadge.className = "status-indicator online";
        statusText.textContent = tr("header.status_connected", { port: data.port || "COM5" }, `接続中 (${data.port || "COM5"})`);
        footerConnectionInfo.textContent = `${data.port} (9600, 8, N, 1) - ONLINE`;
      }
    } else {
      statusBadge.className = "status-indicator";
      statusText.textContent = tr("header.status_disconnected", null, "未接続 (USB確認)");
      footerConnectionInfo.textContent = "OFFLINE";
    }

    const now = new Date();
    footerTimestamp.textContent = now.toLocaleTimeString();

    const batt = data.battery || {};
    const solar = data.solar || {};
    const grid = data.grid || {};
    const inv = data.inverter || {};
    const thermal = data.thermal || {};
    const flow = data.flow || {};
    const settings = data.settings || {};
    const activeFaults = data.active_faults || [];
    const faultHistory = data.fault_history || [];
    const logs = data.logs || [];

    // --- Overview Summary Cards ---
    setText("cardPvPower", solar.pv_total_power ?? 0);
    setText("cardPvVoltage", (solar.pv1_voltage ?? 0).toFixed(1));
    setText("cardPvCurrent", (solar.pv1_current ?? 0).toFixed(2));
    setText("cardPvDailyKwh", (solar.daily_kwh ?? 0).toFixed(2));
    setText("pvStatusTag", (solar.pv_total_power > 10) ? tr("card.pv_active") : tr("card.pv_idle"));

    // Grid AC Input Summary Card
    const gridV = (grid.voltage ?? 0).toFixed(1);
    const gridA = (grid.current ?? 0).toFixed(2);
    const gridW = Math.round(grid.power ?? 0);
    const gridHz = (grid.frequency ?? 50.0).toFixed(1);
    setText("cardGridPower", gridW);
    setText("cardGridVoltage", gridV);
    setText("cardGridCurrent", gridA);
    setText("cardGridHz", gridHz);

    let gridTag = tr("card.grid_offline");
    if (grid.present) {
      if (inv.machine_state_code === 2) {
        gridTag = tr("card.grid_bypass");
      } else if (batt.mains_charge_current > 0.3) {
        gridTag = tr("card.grid_charging");
      } else {
        gridTag = tr("card.grid_standby");
      }
    }
    setText("cardGridStatusTag", gridTag);

    const battSoc = batt.soc ?? 0;
    setText("cardBattSoc", battSoc);
    setText("cardBattVoltage", (batt.voltage ?? 0).toFixed(1));
    const isDischarging = batt.is_discharging || (batt.current < -0.3);
    const isCharging = batt.is_charging || (batt.current > 0.3);
    const absCurrent = Math.abs(batt.current ?? 0).toFixed(1);
    const absPower = Math.abs(batt.power ?? 0).toFixed(0);
    const battDirection = isDischarging ? tr("flow.dir_dis") : (isCharging ? tr("flow.dir_chg") : tr("flow.dir_idle"));

    setText("cardBattCurrent", `${isDischarging ? "-" : (isCharging ? "+" : "")}${absCurrent}`);
    setText("cardBattPower", `${isDischarging ? "-" : (isCharging ? "+" : "")}${absPower}`);
    const battSocBar = document.getElementById("cardBattSocBar");
    if (battSocBar) battSocBar.style.width = `${Math.min(100, Math.max(0, battSoc))}%`;

    const remainingAh = (batt.remaining_ah != null) ? Number(batt.remaining_ah).toFixed(1) : (battSoc * 1.0).toFixed(1);
    const capacityAh = (batt.capacity_ah != null) ? Number(batt.capacity_ah).toFixed(0) : "100";
    const remainingKwh = (batt.remaining_kwh != null) ? Number(batt.remaining_kwh).toFixed(2) : ((batt.remaining_ah || battSoc) * 0.0256).toFixed(2);
    setText("cardBattRemainingAh", remainingAh);
    setText("cardBattCapacityAh", capacityAh);
    setText("cardBattRemainingKwh", remainingKwh);
    setText("cardBattRawSoc", batt.soc_raw_inverter != null ? batt.soc_raw_inverter : "--");

    let battTag = tr("card.batt_standby");
    if (isDischarging) battTag = tr("card.batt_discharging");
    else if (isCharging) battTag = tr("card.batt_charging");
    setText("cardBattModeTag", battTag);

    const battEstTag = document.getElementById("cardBattEstTag");
    if (battEstTag && batt.soc_status_text) {
      if (batt.soc_status_text.includes("収束")) {
        battEstTag.textContent = tr("card.batt_ekf_ocv");
      } else if (batt.soc_status_text.includes("積算")) {
        battEstTag.textContent = tr("card.batt_ekf_coulomb");
      } else if (batt.soc_status_text.includes("校正")) {
        battEstTag.textContent = tr("card.batt_ekf_full");
      }
    }

    const loadW = inv.load_active_power ?? 0;
    setText("cardLoadPower", loadW);
    setText("cardInvVoltage", (inv.voltage ?? 100.0).toFixed(1));
    setText("cardLoadVa", inv.load_apparent_power ?? 0);
    setText("cardLoadPercent", inv.load_percent ?? 0);
    setText("cardLoadPercentTag", tr("card.load_tag", { pct: inv.load_percent ?? 0 }));

    const isBypass = Boolean(flow.is_bypass);
    const isInverting = Boolean(flow.is_inverting);
    const bypassToLoad = Boolean(flow.bypass_to_load);
    const invToLoad = Boolean(flow.inv_to_load);
    const machineCode = inv.machine_state_code ?? 0;

    let cardMachineTitle = tr(`machine_state.${machineCode}`, null, inv.machine_state || "Standby");
    let cardMachineTag = `State ${machineCode}`;
    if (isBypass) {
      cardMachineTitle = tr("card.inv_bypass_title");
      cardMachineTag = tr("card.inv_bypass_tag");
    } else if (isInverting) {
      cardMachineTitle = tr("card.inv_active_title");
      cardMachineTag = tr("card.inv_active_tag");
    }
    setText("cardMachineState", cardMachineTitle);
    setText("cardMachineStateCodeTag", cardMachineTag);
    setText("cardGridStatus", grid.present ? `${gridV}V / ${gridA}A` : tr("card.grid_none"));
    setText("cardBusVoltage", (inv.bus_voltage ?? 0).toFixed(1));
    setText("cardInverterHz", (inv.frequency ?? 50.0).toFixed(1));

    // --- Dynamic SVG Energy Flow Diagram ---
    setText("flowPvPower", `${solar.pv_total_power ?? 0} W`);
    setText("flowPvV", `${(solar.pv1_voltage ?? 0).toFixed(1)} V`);
    setText("flowGridPower", `${(grid.power ?? 0).toFixed(0)} W`);
    setText("flowGridV", grid.present ? `${grid.voltage.toFixed(1)} V (${grid.frequency.toFixed(1)}Hz)` : tr("card.grid_none"));
    setText("flowBusV", tr("flow.bus_line", { bus: (inv.bus_voltage ?? 0).toFixed(1), grid: gridV, hz: gridHz }));
    const translatedMachineState = tr(`machine_state.${machineCode}`, null, inv.machine_state || "Standby");
    setText("flowMachineStateText", tr("flow.mode_label", { state: translatedMachineState, code: machineCode }));
    setText("flowBattSoc", `${battSoc}% (${(batt.voltage ?? 0).toFixed(1)}V)`);
    setText("flowBattFlow", `${battDirection} ${absCurrent} A (${absPower}W)`);
    setText("flowLoadPower", `${loadW} W`);

    // Daily kWh Stats in SVG Diagram & Flow Header
    const dailyPvKwh = (solar.daily_kwh ?? 0).toFixed(3);
    const dailyBattChgKwh = (batt.daily_charge_kwh ?? 0).toFixed(3);
    const dailyBattDisKwh = (batt.daily_discharge_kwh ?? 0).toFixed(3);
    setText("flowPvDailyKwh", tr("flow.daily_pv", { kwh: dailyPvKwh }));
    setText("flowBattDailyKwh", tr("flow.daily_chg", { kwh: dailyBattChgKwh }));
    setText("flowHeaderPvKwh", dailyPvKwh);
    setText("flowHeaderBattChgKwh", dailyBattChgKwh);
    setText("cardBattDailyChgKwh", (batt.daily_charge_kwh ?? 0).toFixed(2));
    setText("cardBattDailyDisKwh", (batt.daily_discharge_kwh ?? 0).toFixed(2));
    setText("flowLoadPercent", tr("card.load_tag", { pct: inv.load_percent ?? 0 }));

    // Dynamic Mode Banner & Inverter Station Internal State
    if (isBypass) {
      if (flowModePill) {
        flowModePill.className = "mode-pill mode-pill-bypass";
        flowModeText.textContent = tr("flow.mode_bypass_pill");
      }
      if (flowStateBadgeBg) {
        flowStateBadgeBg.setAttribute("fill", "rgba(100, 116, 139, 0.2)");
        flowStateBadgeBg.setAttribute("stroke", "#64748b");
      }
      if (flowStateBadgeText) {
        flowStateBadgeText.textContent = tr("flow.inv_standby_badge");
        flowStateBadgeText.setAttribute("fill", "#94a3b8");
      }
      if (bypassRelayGroup) bypassRelayGroup.classList.add("active");
      if (flowNodeInv) {
        flowNodeInv.classList.remove("inverter-active");
        flowNodeInv.classList.add("bypass-active");
      }

      setText("bypassSwitchText", tr("flow.bypass_switch", { w: loadW }));
      setText("flowInvCoreStatus", tr("flow.inv_standby_status"));
      setText("flowInvCoreDesc", tr("flow.inv_bypass_desc"));

    } else if (isInverting) {
      if (flowModePill) {
        flowModePill.className = "mode-pill mode-pill-inverting";
        flowModeText.textContent = tr("flow.mode_inv_pill");
      }
      if (flowStateBadgeBg) {
        flowStateBadgeBg.setAttribute("fill", "rgba(6, 182, 212, 0.25)");
        flowStateBadgeBg.setAttribute("stroke", "#06b6d4");
      }
      if (flowStateBadgeText) {
        flowStateBadgeText.textContent = tr("flow.inv_active_badge");
        flowStateBadgeText.setAttribute("fill", "#bae6fd");
      }
      if (bypassRelayGroup) bypassRelayGroup.classList.remove("active");
      if (flowNodeInv) {
        flowNodeInv.classList.add("inverter-active");
        flowNodeInv.classList.remove("bypass-active");
      }

      setText("bypassSwitchText", tr("flow.bypass_open"));
      setText("flowInvCoreStatus", tr("flow.inv_active_status", { w: loadW }));
      setText("flowInvCoreDesc", tr("flow.inv_active_desc"));

    } else {
      if (flowModePill) {
        flowModePill.className = "mode-pill mode-pill-standby";
        flowModeText.textContent = tr("flow.mode_standby_pill");
      }
      if (flowStateBadgeBg) {
        flowStateBadgeBg.setAttribute("fill", "rgba(100, 116, 139, 0.2)");
        flowStateBadgeBg.setAttribute("stroke", "#64748b");
      }
      if (flowStateBadgeText) {
        flowStateBadgeText.textContent = tr("flow.inv_standby_code", { code: machineCode });
        flowStateBadgeText.setAttribute("fill", "#94a3b8");
      }
      if (bypassRelayGroup) bypassRelayGroup.classList.remove("active");
      if (flowNodeInv) {
        flowNodeInv.classList.remove("inverter-active");
        flowNodeInv.classList.remove("bypass-active");
      }

      setText("bypassSwitchText", tr("flow.bypass_open"));
      setText("flowInvCoreStatus", tr("flow.inv_standby_status"));
      setText("flowInvCoreDesc", tr("flow.ac_off"));
    }

    // Toggle flow line animations
    if (lineSolarToInv) lineSolarToInv.classList.toggle("active", Boolean(flow.solar_to_inv));
    if (lineGridToInv) lineGridToInv.classList.toggle("active", Boolean(flow.grid_to_inv));
    if (lineInvToLoad) lineInvToLoad.classList.toggle("active", invToLoad);
    if (lineBypassToLoad) lineBypassToLoad.classList.toggle("active", bypassToLoad);

    if (tagInvPath) tagInvPath.classList.toggle("active-inv", invToLoad);
    if (tagBypassPath) tagBypassPath.classList.toggle("active-bypass", bypassToLoad);

    // Battery flow animation:
    // If discharging: energy flows Battery -> Inverter (reverse-active animation)
    // If charging: energy flows Inverter/Charger -> Battery (active animation)
    if (isDischarging) {
      lineInvToBatt.classList.remove("active");
      lineInvToBatt.classList.add("reverse-active");
    } else if (isCharging) {
      lineInvToBatt.classList.add("active");
      lineInvToBatt.classList.remove("reverse-active");
    } else {
      lineInvToBatt.classList.remove("active");
      lineInvToBatt.classList.remove("reverse-active");
    }

    // --- Fault Diagnostic Center Updates ---
    updateFaultSection(activeFaults, faultHistory);

    // --- Battery Tab Details ---
    setText("battGaugeVal", battSoc);
    const gaugeCircle = document.getElementById("battGaugeCircle");
    if (gaugeCircle) {
      const offset = 427 * (1 - Math.min(100, Math.max(0, battSoc)) / 100);
      gaugeCircle.style.strokeDashoffset = offset;
      if (battSoc <= 20) gaugeCircle.style.stroke = "#ef4444";
      else if (battSoc <= 40) gaugeCircle.style.stroke = "#f59e0b";
      else gaugeCircle.style.stroke = "#10b981";
    }

    setText("detBattRemainingAh", `${remainingAh} Ah / ${capacityAh}.0 Ah`);
    setText("detBattRemainingKwh", `${remainingKwh} kWh / ${(Number(capacityAh) * 0.0256).toFixed(2)} kWh`);
    setText("detBattV", `${(batt.voltage ?? 0).toFixed(2)} V`);
    setText("detBattA", `${battDirection} ${absCurrent} A`);
    setText("detBattW", `${battDirection} ${absPower} W`);
    setText("detBattTemp", `${batt.temp ?? 25} °C`);
    setText("detBattRawSoc", `${batt.soc_raw_inverter != null ? batt.soc_raw_inverter : "--"} %`);
    setText("detBattSocStatus", batt.soc_status_code ? tr(`battery.ekf_st.${batt.soc_status_code}`, null, batt.soc_status_text) : (batt.soc_status_text || "--"));
    setText("detBattCycles", `${(batt.cycle_count ?? 0).toFixed(2)}`);
    setText("detChargeState", tr(`charge_state.${batt.charge_state_code ?? 0}`, null, batt.charge_state || "--"));
    setText("detPvChargeA", `${(batt.pv_charge_current ?? 0).toFixed(2)} A`);
    setText("detMainsChargeA", `${(batt.mains_charge_current ?? 0).toFixed(2)} A`);
    setText("detTotalChargeW", `${batt.charge_power_total ?? 0} W`);

    // Active Voltage Control Rules (Physical LCD settings)
    setText("detCutoffV", `${(settings.voltage_discharge_cutoff ?? 23.4).toFixed(1)} V`);
    setText("detSwitchMainsV", `${(settings.voltage_switch_to_mains ?? 24.6).toFixed(1)} V`);
    setText("detSwitchInvV", `${(settings.voltage_switch_to_inverter ?? 27.4).toFixed(1)} V`);
    setText("detBoostV", `${(settings.voltage_boost_charge ?? 28.4).toFixed(1)} V`);
    setText("detFloatV", `${(settings.voltage_float_charge ?? 28.4).toFixed(1)} V`);
    setText("detAlarmV", `${(settings.voltage_discharge_alarm ?? 24.4).toFixed(1)} V`);

    // Parallel SOC Rules (Reference only)
    setText("detCutoffSoc", `${settings.soc_discharge_cutoff ?? 5} %`);
    setText("detAlarmSoc", `${settings.soc_discharge_alarm ?? 15} %`);
    setText("detSwitchMainsSoc", `${settings.soc_switch_to_mains ?? 10} %`);
    setText("detSwitchInvSoc", `${settings.soc_switch_to_inverter ?? 100} %`);
    setText("battChemBadge", settings.battery_type_code !== undefined ? tr(`batt_type.${settings.battery_type_code}`, null, settings.battery_type || "LiFePO4") : (settings.battery_type || "LiFePO4"));

    // --- Solar Tab Details ---
    setText("detPv1Power", solar.pv1_power ?? 0);
    setText("detPv1Voltage", `${(solar.pv1_voltage ?? 0).toFixed(1)} V`);
    setText("detPv1Current", `${(solar.pv1_current ?? 0).toFixed(2)} A`);
    setText("detPvDailyKwh", `${(solar.daily_kwh ?? 0).toFixed(3)} kWh`);
    setText("detPv2V", `${(solar.pv2_voltage ?? 0).toFixed(1)} V`);
    setText("detPv2A", `${(solar.pv2_current ?? 0).toFixed(2)} A`);
    setText("detPv2W", `${solar.pv2_power ?? 0} W`);

    // --- Inverter & Load Tab Details ---
    setText("detInvV", `${(inv.voltage ?? 100.0).toFixed(1)} V`);
    setText("detInvA", `${(inv.current ?? 0).toFixed(2)} A`);
    setText("detInvHz", `${(inv.frequency ?? 50.0).toFixed(2)} Hz`);
    setText("detBusV", `${(inv.bus_voltage ?? 0).toFixed(1)} V`);
    setText("detLoadW", `${loadW} W`);
    setText("detLoadVa", `${inv.load_apparent_power ?? 0} VA`);
    setText("detLoadA", `${(inv.load_current ?? 0).toFixed(2)} A`);
    setText("detLoadPct", `${inv.load_percent ?? 0} %`);
    const detLoadBar = document.getElementById("detLoadBar");
    if (detLoadBar) detLoadBar.style.width = `${Math.min(100, inv.load_percent ?? 0)}%`;

    // --- Grid Tab Details ---
    setText("detGridV", (grid.voltage ?? 0).toFixed(1));
    setText("detGridA", (grid.current ?? 0).toFixed(2));
    setText("detGridHz", (grid.frequency ?? 0).toFixed(2));
    setText("detGridW", Math.round(grid.power ?? 0));
    const gridBadge = document.getElementById("detGridOnlineBadge");
    if (gridBadge) {
      gridBadge.textContent = grid.present ? tr("grid.online") : tr("grid.offline");
      gridBadge.style.color = grid.present ? "#a855f7" : "#9ca3af";
    }

    // --- Thermal Tab Details ---
    setText("tempAVal", `${(thermal.temp_a_dcdc ?? 0).toFixed(1)} °C`);
    setText("tempBVal", `${(thermal.temp_b_inv ?? 0).toFixed(1)} °C`);
    setText("tempCVal", `${(thermal.temp_c_mppt ?? 0).toFixed(1)} °C`);

    // --- Terminal Logs Update ---
    syncTerminalLogs(logs);

    // --- Settings Tab Population ---
    renderSettingsTable(settings);

    // --- Auto-Rescue UI & Charge Priority Update ---
    updateAutoRescueUI(data.auto_rescue, data.settings);

    // --- Push to Power Chart State ---
    onLiveTelemetryReceived(now, solar.pv_total_power ?? 0, loadW, batt.power ?? 0, grid.power ?? 0, battSoc);
  }

  // -------------------------------------------------------------------------
  // Faults & Diagnostic Updates
  // -------------------------------------------------------------------------
  function getFaultText(f) {
    const isEn = window.I18N ? window.I18N.getLang() !== "ja" : false;
    return {
      name: isEn ? (f.name_en || f.symbol || f.name_ja) : (f.name_ja || f.symbol),
      cause: isEn ? (f.cause_en || f.cause) : f.cause,
      action: isEn ? (f.action_en || f.action) : f.action,
    };
  }

  function updateFaultSection(activeFaults, faultHistory) {
    // 1. Top Banner & Nav Badge
    if (activeFaults.length > 0) {
      topFaultBanner.classList.remove("hidden");
      navFaultBadge.classList.remove("hidden");
      navFaultBadge.textContent = activeFaults.length;

      const firstFault = activeFaults[0];
      const ft = getFaultText(firstFault);
      topFaultTitle.textContent = `🚨 Code ${firstFault.code} - ${firstFault.symbol} (${ft.name})`;
      topFaultDesc.textContent = `${tr("faults.cause_lbl")} ${ft.cause} | ${tr("faults.action_lbl")} ${ft.action}`;
      activeFaultCountBadge.textContent = tr("faults.count_active", { n: activeFaults.length });
      activeFaultCountBadge.style.background = "#ef4444";
      activeFaultCountBadge.style.color = "#fff";
    } else {
      topFaultBanner.classList.add("hidden");
      navFaultBadge.classList.add("hidden");
      activeFaultCountBadge.textContent = tr("faults.count_normal");
      activeFaultCountBadge.style.background = "rgba(16, 185, 129, 0.2)";
      activeFaultCountBadge.style.color = "#34d399";
    }

    // 2. Active Fault Cards Grid
    if (activeFaults.length === 0) {
      activeFaultsList.innerHTML = `
        <div class="all-normal-card">
          <div class="icon">✅</div>
          <h4>${escapeHtml(tr("faults.normal_title"))}</h4>
          <p>${escapeHtml(tr("faults.normal_desc"))}</p>
        </div>`;
    } else {
      activeFaultsList.innerHTML = activeFaults.map(f => {
        const sevClass = f.level === "critical" ? "critical" : (f.level === "warning" ? "warning" : "info");
        const sevBadge = f.level === "critical" ? "badge-critical" : (f.level === "warning" ? "badge-warning" : "badge-info");
        const sevText = f.level === "critical" ? tr("faults.sev_critical") : (f.level === "warning" ? tr("faults.sev_warning") : tr("faults.sev_info"));
        const ft = getFaultText(f);

        return `
          <div class="diagnostic-card ${sevClass}">
            <div class="card-header-row">
              <span class="card-code-tag">CODE ${String(f.code).padStart(2, '0')}</span>
              <span class="card-severity-badge ${sevBadge}">${sevText}</span>
            </div>
            <div class="diag-title">${escapeHtml(ft.name)} (${escapeHtml(f.symbol)})</div>
            <div class="diag-desc-box">
              <div><strong>${escapeHtml(tr("faults.cause_lbl"))}</strong> ${escapeHtml(ft.cause)}</div>
              <div><strong>${escapeHtml(tr("faults.action_lbl"))}</strong> ${escapeHtml(ft.action)}</div>
            </div>
          </div>`;
      }).join("");
    }

    // 3. Historical Fault Log
    if (faultHistory.length > 0) {
      faultHistoryBody.innerHTML = faultHistory.slice().reverse().map(f => {
        const sevBadge = f.level === "critical" ? "badge-critical" : "badge-warning";
        const sevText = f.level === "critical" ? tr("faults.sev_critical") : tr("faults.sev_warning");
        const ft = getFaultText(f);
        return `
          <tr>
            <td><code>${f.timestamp || "--:--:--"}</code></td>
            <td><code>Code ${String(f.code).padStart(2, '0')}</code></td>
            <td><strong>${escapeHtml(f.symbol)}</strong></td>
            <td><strong class="text-cyan">${escapeHtml(ft.name)}</strong></td>
            <td><span class="card-severity-badge ${sevBadge}">${sevText}</span></td>
            <td>${escapeHtml(ft.cause)}</td>
            <td class="text-muted">${escapeHtml(ft.action)}</td>
          </tr>`;
      }).join("");
    }
  }

  // -------------------------------------------------------------------------
  // Load & Filter Full Fault Encyclopedia
  // -------------------------------------------------------------------------
  async function loadFaultEncyclopedia() {
    try {
      const res = await fetch("/api/faults/database");
      faultDatabaseCache = await res.json();
      renderFaultEncyclopedia(faultDatabaseCache);
    } catch (err) {
      console.warn("Failed to load fault encyclopedia:", err);
    }
  }

  function renderFaultEncyclopedia(db) {
    if (!faultEncyclopediaBody) return;
    const entries = Object.values(db).sort((a, b) => a.code - b.code);

    faultEncyclopediaBody.innerHTML = entries.map(item => {
      const sevBadge = item.level === "critical" ? "badge-critical" : (item.level === "warning" ? "badge-warning" : "badge-info");
      const sevText = item.level === "critical" ? tr("faults.sev_critical") : (item.level === "warning" ? tr("faults.sev_warning") : tr("faults.sev_info"));
      const ft = getFaultText(item);

      return `
        <tr>
          <td><code>Code ${String(item.code).padStart(2, '0')}</code></td>
          <td><strong>${escapeHtml(item.symbol)}</strong></td>
          <td><strong class="text-cyan">${escapeHtml(ft.name)}</strong></td>
          <td><span class="card-severity-badge ${sevBadge}">${sevText}</span></td>
          <td style="font-size: 0.8rem;">${escapeHtml(ft.cause)}</td>
          <td style="font-size: 0.8rem;" class="text-muted">${escapeHtml(ft.action)}</td>
        </tr>`;
    }).join("");
  }

  function filterFaultEncyclopedia() {
    const q = faultSearchInput.value.trim().toLowerCase();
    if (!q) {
      renderFaultEncyclopedia(faultDatabaseCache);
      return;
    }
    const filtered = Object.values(faultDatabaseCache).filter(item => {
      return String(item.code).includes(q) ||
        item.symbol.toLowerCase().includes(q) ||
        item.name_ja.toLowerCase().includes(q) ||
        item.cause.toLowerCase().includes(q) ||
        item.action.toLowerCase().includes(q);
    });
    renderFaultEncyclopedia(filtered);
  }

  // -------------------------------------------------------------------------
  // Live Terminal Logs Management
  // -------------------------------------------------------------------------
  let lastLoggedMessage = "";
  function syncTerminalLogs(logs) {
    if (!terminalWindow || !logs || logs.length === 0) return;

    const latest = logs[logs.length - 1];
    const key = `${latest.time}_${latest.message}`;
    if (key === lastLoggedMessage) return;
    lastLoggedMessage = key;

    terminalWindow.innerHTML = logs.map(l => {
      const lvl = l.level.toLowerCase();
      let colorClass = "info";
      if (lvl === "warn" || lvl === "warning") colorClass = "warn";
      else if (lvl === "error" || lvl === "critical") colorClass = "error";
      else if (lvl === "success") colorClass = "success";

      return `<div class="terminal-line ${colorClass}">[${escapeHtml(l.time)}] [${escapeHtml(l.level)}] ${escapeHtml(l.message)}</div>`;
    }).join("");

    if (chkAutoScroll && chkAutoScroll.checked) {
      terminalWindow.scrollTop = terminalWindow.scrollHeight;
    }
  }

  function addTerminalLog(level, message) {
    if (!terminalWindow) return;
    const nowStr = new Date().toLocaleTimeString();
    const lvl = level.toLowerCase();
    const div = document.createElement("div");
    div.className = `terminal-line ${lvl}`;
    div.textContent = `[${nowStr}] [${level}] ${message}`;
    terminalWindow.appendChild(div);
    if (chkAutoScroll && chkAutoScroll.checked) {
      terminalWindow.scrollTop = terminalWindow.scrollHeight;
    }
  }

  // -------------------------------------------------------------------------
  // Settings Table Renderer
  // -------------------------------------------------------------------------
  let settingsRendered = false;
  function renderSettingsTable(settings) {
    if (settingsRendered || !settings || Object.keys(settings).length === 0) return;
    settingsRendered = true;

    const tbody = document.getElementById("settingsTableBody");
    if (!tbody) return;

    const outPrioVal = settings.output_priority_code !== undefined ? tr(`output_prio.${settings.output_priority_code}`, null, settings.output_priority) : settings.output_priority;
    const chgPrioVal = settings.charge_priority_code !== undefined ? tr(`charge_prio.${settings.charge_priority_code}`, null, settings.charge_priority) : settings.charge_priority;
    const battTypeVal = settings.battery_type_code !== undefined ? tr(`batt_type.${settings.battery_type_code}`, null, settings.battery_type) : settings.battery_type;
    const acRangeVal = settings.ac_input_voltage_range_code !== undefined ? tr(`ac_range.${settings.ac_input_voltage_range_code}`, null, settings.ac_input_voltage_range) : settings.ac_input_voltage_range;
    const ecoVal = settings.eco_mode_code !== undefined ? tr(settings.eco_mode_code === 1 ? "common.on" : "common.off") : settings.eco_mode;
    const ovlVal = settings.overload_restart_code !== undefined ? tr(settings.overload_restart_code === 1 ? "common.on" : "common.off") : settings.overload_restart;
    const ovtVal = settings.overheat_restart_code !== undefined ? tr(settings.overheat_restart_code === 1 ? "common.on" : "common.off") : settings.overheat_restart;

    const rows = [
      { reg: "0xE204", name: tr("settings.r_out_prio"), val: outPrioVal, desc: tr("settings.d_out_prio") },
      { reg: "0xE20F", name: tr("settings.r_chg_prio"), val: chgPrioVal, desc: tr("settings.d_chg_prio") },
      { reg: "0xE004", name: tr("settings.r_batt_type"), val: battTypeVal, desc: tr("settings.d_batt_type") },
      { reg: "0xE205", name: tr("settings.r_ac_chg_max"), val: `${settings.mains_charge_current_limit} A`, desc: tr("settings.d_ac_chg_max") },
      { reg: "0xE20A", name: tr("settings.r_tot_chg_max"), val: `${settings.max_total_charge_current} A`, desc: tr("settings.d_tot_chg_max") },
      { reg: "0xE208", name: tr("settings.r_out_v"), val: `${settings.output_voltage_setting} V`, desc: tr("settings.d_out_v") },
      { reg: "0xE209", name: tr("settings.r_out_hz"), val: `${settings.output_freq_setting} Hz`, desc: tr("settings.d_out_hz") },
      { reg: "0xE00E", name: tr("settings.r_v_cut"), val: `${settings.voltage_discharge_cutoff ?? 23.4} V`, desc: tr("settings.d_v_cut") },
      { reg: "0xE01B", name: tr("settings.r_v_mains"), val: `${settings.voltage_switch_to_mains ?? 24.6} V`, desc: tr("settings.d_v_mains") },
      { reg: "0xE022", name: tr("settings.r_v_inv"), val: `${settings.voltage_switch_to_inverter ?? 27.4} V`, desc: tr("settings.d_v_inv") },
      { reg: "0xE00C", name: tr("settings.r_v_alarm"), val: `${settings.voltage_discharge_alarm ?? 24.4} V`, desc: tr("settings.d_v_alarm") },
      { reg: "0xE008", name: tr("settings.r_v_boost"), val: `${settings.voltage_boost_charge ?? 28.4} V`, desc: tr("settings.d_v_boost") },
      { reg: "0xE009", name: tr("settings.r_v_float"), val: `${settings.voltage_float_charge ?? 28.4} V`, desc: tr("settings.d_v_float") },
      { reg: "0xE00F", name: tr("settings.r_s_cut"), val: `${settings.soc_discharge_cutoff} %`, desc: tr("settings.d_s_cut") },
      { reg: "0xE01D", name: tr("settings.r_s_chg"), val: `${settings.soc_charge_cutoff} %`, desc: tr("settings.d_s_chg") },
      { reg: "0xE01F", name: tr("settings.r_s_mains"), val: `${settings.soc_switch_to_mains} %`, desc: tr("settings.d_s_mains") },
      { reg: "0xE020", name: tr("settings.r_s_inv"), val: `${settings.soc_switch_to_inverter} %`, desc: tr("settings.d_s_inv") },
      { reg: "0xE20B", name: tr("settings.r_ac_range"), val: acRangeVal, desc: tr("settings.d_ac_range") },
      { reg: "0xE20C", name: tr("settings.r_eco"), val: ecoVal, desc: tr("settings.d_eco") },
      { reg: "0xE20D", name: tr("settings.r_ovl"), val: ovlVal, desc: tr("settings.d_ovl") },
      { reg: "0xE20E", name: tr("settings.r_ovt"), val: ovtVal, desc: tr("settings.d_ovt") },
    ];

    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><code>${r.reg}</code></td>
        <td><strong>${escapeHtml(r.name)}</strong></td>
        <td><strong class="text-cyan">${escapeHtml(r.val)}</strong></td>
        <td class="text-muted">${escapeHtml(r.desc)}</td>
      </tr>`).join("");
  }

  // =========================================================================
  // 24-Hour & Real-Time Power Chart Management
  // =========================================================================
  function updateChartsLocale() {
    if (powerChart && powerChart.data && powerChart.data.datasets.length >= 5) {
      powerChart.data.datasets[0].label = tr("chart.ds_pv");
      powerChart.data.datasets[1].label = tr("chart.ds_load");
      powerChart.data.datasets[2].label = tr("chart.ds_batt");
      powerChart.data.datasets[3].label = tr("chart.ds_grid");
      powerChart.data.datasets[4].label = tr("chart.ds_soc");
      powerChart.update("none");
    }
    if (dayDetailChart && dayDetailChart.data && dayDetailChart.data.datasets.length >= 5) {
      dayDetailChart.data.datasets[0].label = tr("chart.ds_pv");
      dayDetailChart.data.datasets[1].label = tr("chart.ds_load");
      dayDetailChart.data.datasets[2].label = tr("chart.ds_batt");
      dayDetailChart.data.datasets[3].label = tr("chart.ds_grid");
      dayDetailChart.data.datasets[4].label = tr("chart.ds_soc");
      dayDetailChart.update("none");
    }
    if (dailyBarChart && dailyBarChart.data && dailyBarChart.data.datasets.length >= 3) {
      dailyBarChart.data.datasets[0].label = tr("chart.ds_pv_kwh");
      dailyBarChart.data.datasets[1].label = tr("chart.ds_chg_kwh");
      dailyBarChart.data.datasets[2].label = tr("chart.ds_load_kwh");
      if (dailyBarChart.options?.scales?.y?.title) {
        dailyBarChart.options.scales.y.title.text = tr("chart.axis_kwh");
      }
      dailyBarChart.update("none");
    }
  }

  function initPowerChart() {
    const ctx = document.getElementById("powerChart");
    if (!ctx) return;

    powerChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: tr("chart.ds_pv"),
            data: [],
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.12)",
            fill: true,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 6,
          },
          {
            label: tr("chart.ds_load"),
            data: [],
            borderColor: "#ec4899",
            backgroundColor: "rgba(236, 72, 153, 0.08)",
            fill: true,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 6,
          },
          {
            label: tr("chart.ds_batt"),
            data: [],
            borderColor: "#64748b",
            backgroundColor: "transparent",
            tension: 0.25,
            borderWidth: 1.5,
            borderDash: [3, 3],
            pointRadius: 0,
            pointHitRadius: 6,
          },
          {
            label: tr("chart.ds_grid"),
            data: [],
            borderColor: "#a855f7",
            backgroundColor: "transparent",
            tension: 0.25,
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
            pointHitRadius: 6,
          },
          {
            label: tr("chart.ds_soc"),
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16, 185, 129, 0.08)",
            tension: 0.25,
            borderWidth: 2.5,
            pointRadius: 0,
            pointHitRadius: 6,
            yAxisID: "y1",
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 0 },
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.04)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 10, family: "'JetBrains Mono', monospace" },
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 14
            }
          },
          y: {
            position: "left",
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 10, family: "'JetBrains Mono', monospace" }
            },
            suggestedMin: 0,
            suggestedMax: 1000
          },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            grid: { drawOnChartArea: false },
            ticks: {
              color: "#10b981",
              font: { size: 10, family: "'JetBrains Mono', monospace" },
              callback: function(val) { return val + "%"; }
            }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: "index",
            intersect: false,
            backgroundColor: "rgba(11, 15, 23, 0.95)",
            borderColor: "rgba(255, 255, 255, 0.15)",
            borderWidth: 1,
            titleColor: "#f9fafb",
            titleFont: { size: 12, family: "'JetBrains Mono', monospace" },
            bodyColor: "#e5e7eb",
            bodyFont: { size: 11 },
            padding: 10,
            callbacks: {
              label: function(context) {
                let label = context.dataset.label || "";
                if (label) label += ": ";
                if (context.parsed.y !== null) {
                  if (context.dataset.yAxisID === "y1") {
                    label += context.parsed.y.toFixed(1) + " %";
                  } else {
                    label += Math.round(context.parsed.y) + " W";
                  }
                }
                return label;
              }
            }
          }
        }
      }
    });

    // Chart Range Selector Buttons Listener
    const rangeContainer = document.getElementById("chartRangeSelector");
    if (rangeContainer) {
      rangeContainer.querySelectorAll(".btn-range").forEach(btn => {
        btn.addEventListener("click", () => {
          rangeContainer.querySelectorAll(".btn-range").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          currentChartRange = btn.getAttribute("data-range") || "24h";
          updateChartRangeBadge();
          renderPowerChart();
        });
      });
    }
  }

  function updateChartRangeBadge() {
    const badge = document.getElementById("chartRangeBadge");
    if (!badge) return;
    const keyMap = {
      "24h": "chart.range_24h_desc",
      "12h": "chart.range_12h_desc",
      "6h": "chart.range_6h_desc",
      "1h": "chart.range_1h_desc",
      "live": "chart.range_live_desc"
    };
    badge.textContent = tr(keyMap[currentChartRange] || "chart.range_24h_desc");
  }

  async function load24hHistory() {
    try {
      const res = await fetch("/api/history/24h");
      if (res.ok) {
        rawHistory24h = await res.json();
        renderPowerChart();
      }
    } catch (e) {
      console.warn("Failed to load 24h history:", e);
    }
  }

  // Refresh 24h history every 60 seconds
  setInterval(() => {
    if (currentChartRange !== "live") {
      load24hHistory();
    }
  }, 60000);

  function onLiveTelemetryReceived(nowDt, pvW, loadW, battW, gridW, battSoc) {
    const timeStr = nowDt.toLocaleTimeString();

    // 1. Maintain live 60-point buffer
    liveHistory.push({
      time: timeStr,
      pv_w: pvW,
      load_w: loadW,
      batt_w: battW,
      grid_w: gridW,
      soc: battSoc
    });
    if (liveHistory.length > 60) liveHistory.shift();

    // 2. If in live range mode, update powerChart immediately
    if (currentChartRange === "live" && powerChart) {
      powerChart.data.labels = liveHistory.map(p => p.time);
      powerChart.data.datasets[0].data = liveHistory.map(p => p.pv_w);
      powerChart.data.datasets[1].data = liveHistory.map(p => p.load_w);
      powerChart.data.datasets[2].data = liveHistory.map(p => p.batt_w);
      powerChart.data.datasets[3].data = liveHistory.map(p => p.grid_w);
      if (powerChart.data.datasets[4]) {
        powerChart.data.datasets[4].data = liveHistory.map(p => p.soc);
      }
      powerChart.update("none");
    }
  }

  function renderPowerChart() {
    if (!powerChart) return;

    if (currentChartRange === "live") {
      powerChart.data.labels = liveHistory.map(p => p.time);
      powerChart.data.datasets[0].data = liveHistory.map(p => p.pv_w);
      powerChart.data.datasets[1].data = liveHistory.map(p => p.load_w);
      powerChart.data.datasets[2].data = liveHistory.map(p => p.batt_w);
      powerChart.data.datasets[3].data = liveHistory.map(p => p.grid_w);
      if (powerChart.data.datasets[4]) {
        powerChart.data.datasets[4].data = liveHistory.map(p => p.soc);
      }
      powerChart.update("none");
      return;
    }

    // Minutes count based on selected range
    const rangeMinutesMap = { "24h": 1440, "12h": 720, "6h": 360, "1h": 60 };
    const limit = rangeMinutesMap[currentChartRange] || 1440;
    const points = rawHistory24h.slice(-limit);

    powerChart.data.labels = points.map(p => p.time || p.ts.substr(11, 5));
    powerChart.data.datasets[0].data = points.map(p => p.pv_w ?? 0);
    powerChart.data.datasets[1].data = points.map(p => p.load_w ?? 0);
    powerChart.data.datasets[2].data = points.map(p => p.batt_w ?? 0);
    powerChart.data.datasets[3].data = points.map(p => p.grid_w ?? 0);
    if (powerChart.data.datasets[4]) {
      powerChart.data.datasets[4].data = points.map(p => p.soc ?? null);
    }
    powerChart.update();
  }

  // =========================================================================
  // TAB: Daily Generation & Charging History Management
  // =========================================================================
  function initHistoryTab() {
    initDayDetailChart();
    initDailyBarChart();

    const dateSelect = document.getElementById("historyDateSelect");
    if (dateSelect) {
      dateSelect.addEventListener("change", (e) => {
        currentSelectedDate = e.target.value;
        loadDayDetail(currentSelectedDate);
      });
    }

    const btnPrev = document.getElementById("btnHistPrevDay");
    if (btnPrev) {
      btnPrev.addEventListener("click", () => {
        if (!dateSelect || dateSelect.options.length <= 1) return;
        const curIdx = dateSelect.selectedIndex;
        if (curIdx < dateSelect.options.length - 1) {
          dateSelect.selectedIndex = curIdx + 1;
          currentSelectedDate = dateSelect.value;
          loadDayDetail(currentSelectedDate);
        }
      });
    }

    const btnNext = document.getElementById("btnHistNextDay");
    if (btnNext) {
      btnNext.addEventListener("click", () => {
        if (!dateSelect || dateSelect.options.length <= 1) return;
        const curIdx = dateSelect.selectedIndex;
        if (curIdx > 0) {
          dateSelect.selectedIndex = curIdx - 1;
          currentSelectedDate = dateSelect.value;
          loadDayDetail(currentSelectedDate);
        }
      });
    }

    const btnToday = document.getElementById("btnHistToday");
    if (btnToday) {
      btnToday.addEventListener("click", () => {
        if (!dateSelect || dateSelect.options.length === 0) return;
        dateSelect.selectedIndex = 0;
        currentSelectedDate = dateSelect.value;
        loadDayDetail(currentSelectedDate);
      });
    }

    const btnExport = document.getElementById("btnExportHistCsv");
    if (btnExport) {
      btnExport.addEventListener("click", () => {
        const targetDate = currentSelectedDate || (dateSelect ? dateSelect.value : "");
        if (targetDate) {
          window.location.href = `/api/log/export?date=${targetDate}`;
        }
      });
    }
  }

  function initDayDetailChart() {
    const ctx = document.getElementById("dayDetailChart");
    if (!ctx) return;

    dayDetailChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: tr("chart.ds_pv"),
            data: [],
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.12)",
            fill: true,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: 0,
          },
          {
            label: tr("chart.ds_load"),
            data: [],
            borderColor: "#ec4899",
            backgroundColor: "rgba(236, 72, 153, 0.08)",
            fill: true,
            tension: 0.25,
            borderWidth: 2,
            pointRadius: 0,
          },
          {
            label: tr("chart.ds_batt"),
            data: [],
            borderColor: "#64748b",
            backgroundColor: "transparent",
            tension: 0.25,
            borderWidth: 1.5,
            borderDash: [3, 3],
            pointRadius: 0,
          },
          {
            label: tr("chart.ds_grid"),
            data: [],
            borderColor: "#a855f7",
            backgroundColor: "transparent",
            tension: 0.25,
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
          },
          {
            label: tr("chart.ds_soc"),
            data: [],
            borderColor: "#10b981",
            backgroundColor: "rgba(16, 185, 129, 0.08)",
            tension: 0.25,
            borderWidth: 2.5,
            pointRadius: 0,
            yAxisID: "y1",
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.04)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 10, family: "'JetBrains Mono', monospace" },
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 14
            }
          },
          y: {
            position: "left",
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 10, family: "'JetBrains Mono', monospace" }
            },
            suggestedMin: 0,
            suggestedMax: 1000
          },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            grid: { drawOnChartArea: false },
            ticks: {
              color: "#10b981",
              font: { size: 10, family: "'JetBrains Mono', monospace" },
              callback: function(val) { return val + "%"; }
            }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: "index",
            intersect: false,
            backgroundColor: "rgba(11, 15, 23, 0.95)",
            borderColor: "rgba(255, 255, 255, 0.15)",
            borderWidth: 1,
            titleColor: "#f9fafb",
            titleFont: { size: 12, family: "'JetBrains Mono', monospace" },
            bodyColor: "#e5e7eb",
            bodyFont: { size: 11 },
            padding: 10,
            callbacks: {
              label: function(context) {
                let label = context.dataset.label || "";
                if (label) label += ": ";
                if (context.parsed.y !== null) {
                  if (context.dataset.yAxisID === "y1") {
                    label += context.parsed.y.toFixed(1) + " %";
                  } else {
                    label += Math.round(context.parsed.y) + " W";
                  }
                }
                return label;
              }
            }
          }
        }
      }
    });
  }

  function initDailyBarChart() {
    const ctx = document.getElementById("dailyBarChart");
    if (!ctx) return;

    dailyBarChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: [],
        datasets: [
          {
            label: tr("chart.ds_pv_kwh"),
            data: [],
            backgroundColor: "rgba(245, 158, 11, 0.8)",
            borderColor: "#f59e0b",
            borderWidth: 1,
            borderRadius: 4,
          },
          {
            label: tr("chart.ds_chg_kwh"),
            data: [],
            backgroundColor: "rgba(16, 185, 129, 0.8)",
            borderColor: "#10b981",
            borderWidth: 1,
            borderRadius: 4,
          },
          {
            label: tr("chart.ds_load_kwh"),
            data: [],
            backgroundColor: "rgba(236, 72, 153, 0.8)",
            borderColor: "#ec4899",
            borderWidth: 1,
            borderRadius: 4,
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            grid: { color: "rgba(255, 255, 255, 0.04)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 11, family: "'JetBrains Mono', monospace" }
            }
          },
          y: {
            grid: { color: "rgba(255, 255, 255, 0.05)" },
            ticks: {
              color: "#9ca3af",
              font: { size: 10, family: "'JetBrains Mono', monospace" }
            },
            title: {
              display: true,
              text: tr("chart.axis_kwh"),
              color: "#9ca3af",
              font: { size: 10 }
            },
            suggestedMin: 0
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            mode: "index",
            intersect: false,
            backgroundColor: "rgba(11, 15, 23, 0.95)",
            borderColor: "rgba(255, 255, 255, 0.15)",
            borderWidth: 1,
            titleColor: "#f9fafb",
            titleFont: { size: 12, family: "'JetBrains Mono', monospace" },
            bodyColor: "#e5e7eb",
            bodyFont: { size: 11 },
            padding: 10,
            callbacks: {
              label: function(context) {
                let label = context.dataset.label || "";
                if (label) label += ": ";
                if (context.parsed.y !== null) {
                  label += context.parsed.y.toFixed(3) + " kWh";
                }
                return label;
              }
            }
          }
        }
      }
    });
  }

  async function loadDailyHistory() {
    try {
      const res = await fetch("/api/history/days");
      if (!res.ok) return;
      dailySummaryRecords = await res.json();

      // 1. Update Date Selector dropdown
      const dateSelect = document.getElementById("historyDateSelect");
      if (dateSelect) {
        const prevSelected = currentSelectedDate || dateSelect.value;
        dateSelect.innerHTML = "";
        dailySummaryRecords.forEach((rec, idx) => {
          const opt = document.createElement("option");
          opt.value = rec.date;
          const isToday = idx === 0;
          opt.textContent = `${rec.date}${isToday ? tr("history.opt_today") : ""}`;
          dateSelect.appendChild(opt);
        });

        if (prevSelected && dailySummaryRecords.some(r => r.date === prevSelected)) {
          dateSelect.value = prevSelected;
          currentSelectedDate = prevSelected;
        } else if (dailySummaryRecords.length > 0) {
          currentSelectedDate = dailySummaryRecords[0].date;
          dateSelect.value = currentSelectedDate;
        }
      }

      // 2. Update Badge count
      const countBadge = document.getElementById("histDaysCountBadge");
      if (countBadge) countBadge.textContent = tr("history.days_recorded", { n: dailySummaryRecords.length });

      // 3. Render Daily Bar Chart (chronological from oldest to newest)
      if (dailyBarChart) {
        const chronoRecords = dailySummaryRecords.slice().reverse();
        dailyBarChart.data.labels = chronoRecords.map(r => r.date.substr(5)); // "10-28"
        dailyBarChart.data.datasets[0].data = chronoRecords.map(r => r.pv_kwh ?? 0);
        dailyBarChart.data.datasets[1].data = chronoRecords.map(r => r.batt_chg_kwh ?? 0);
        dailyBarChart.data.datasets[2].data = chronoRecords.map(r => r.load_kwh ?? 0);
        dailyBarChart.update();
      }

      // 4. Render History Table
      renderHistoryTable(dailySummaryRecords);

      // 5. Load detail for selected date
      if (currentSelectedDate) {
        loadDayDetail(currentSelectedDate);
      }
    } catch (e) {
      console.warn("Error loading daily history:", e);
    }
  }

  async function loadDayDetail(dateStr) {
    if (!dateStr) return;
    try {
      setText("histDetailTitle", `${dateStr}`);
      const res = await fetch(`/api/history/day?date=${dateStr}`);
      if (!res.ok) return;
      const data = await res.json();
      const summary = data.summary || {};
      const points = data.points || [];

      // Update Summary Cards for this selected date
      setText("histPvKwh", (summary.pv_kwh ?? 0).toFixed(3));
      setText("histPeakPvW", summary.peak_pv_w ?? 0);
      setText("histBattChgKwh", (summary.batt_chg_kwh ?? 0).toFixed(3));
      setText("histBattDisKwh", (summary.batt_dis_kwh ?? 0).toFixed(3));
      setText("histSocRange", `${summary.min_soc ?? 0}% 〜 ${summary.max_soc ?? 0}%`);
      setText("histLoadKwh", (summary.load_kwh ?? 0).toFixed(3));
      setText("histPeakLoadW", summary.peak_load_w ?? 0);

      // Update Day Detail Timeline Chart
      if (dayDetailChart && points.length > 0) {
        dayDetailChart.data.labels = points.map(p => p.time || p.ts.substr(11, 5));
        dayDetailChart.data.datasets[0].data = points.map(p => p.pv_w ?? 0);
        dayDetailChart.data.datasets[1].data = points.map(p => p.load_w ?? 0);
        dayDetailChart.data.datasets[2].data = points.map(p => p.batt_w ?? 0);
        dayDetailChart.data.datasets[3].data = points.map(p => p.grid_w ?? 0);
        if (dayDetailChart.data.datasets[4]) {
          dayDetailChart.data.datasets[4].data = points.map(p => p.soc ?? null);
        }
        dayDetailChart.update();
      }
    } catch (e) {
      console.warn(`Error loading day detail for ${dateStr}:`, e);
    }
  }

  function renderHistoryTable(records) {
    const tbody = document.getElementById("dailyHistoryTableBody");
    if (!tbody) return;

    if (!records || records.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" class="text-center text-muted">${escapeHtml(tr("history.no_data"))}</td></tr>`;
      return;
    }

    tbody.innerHTML = records.map(r => `
      <tr>
        <td><strong>${r.date}</strong></td>
        <td><strong style="color: #fbbf24">${(r.pv_kwh ?? 0).toFixed(3)}</strong> kWh</td>
        <td><strong style="color: #34d399">${(r.batt_chg_kwh ?? 0).toFixed(3)}</strong> kWh</td>
        <td><strong style="color: #10b981">${(r.batt_dis_kwh ?? 0).toFixed(3)}</strong> kWh</td>
        <td><strong style="color: #f472b6">${(r.load_kwh ?? 0).toFixed(3)}</strong> kWh</td>
        <td>${r.peak_pv_w ?? 0} W</td>
        <td>${r.peak_load_w ?? 0} W</td>
        <td><strong style="color: #34d399">${r.min_soc ?? 0}% 〜 ${r.max_soc ?? 0}%</strong></td>
        <td>
          <a href="/api/log/export?date=${r.date}" class="btn btn-outline btn-sm" download style="text-decoration:none; display:inline-block; padding: 3px 8px;">📥 CSV</a>
        </td>
      </tr>
    `).join("");
  }

  // -------------------------------------------------------------------------
  // Modbus Register Scanner
  // -------------------------------------------------------------------------
  async function runRegisterScan() {
    const startInput = document.getElementById("scanStartAddr").value.trim();
    let count = parseInt(document.getElementById("scanCount").value) || 16;
    count = Math.min(64, Math.max(1, count));

    let startAddr = 0;
    if (startInput.startsWith("0x") || startInput.startsWith("0X")) {
      startAddr = parseInt(startInput, 16);
    } else {
      startAddr = parseInt(startInput, 10);
    }

    if (isNaN(startAddr)) {
      alert(tr("scanner.invalid_addr"));
      return;
    }

    const tbody = document.getElementById("scannerTableBody");
    tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">${escapeHtml(tr("scanner.scanning", { hex: startAddr.toString(16).toUpperCase(), count }))}</td></tr>`;

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start_addr: startAddr, count: count })
      });
      const data = await res.json();
      const results = data.results || [];

      if (results.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">${escapeHtml(tr("scanner.no_result"))}</td></tr>`;
        return;
      }

      tbody.innerHTML = results.map(r => {
        const isOk = r.status === "OK";
        const statusBadge = isOk
          ? `<span style="color:#10b981;font-weight:600">${escapeHtml(tr("scanner.ok"))}</span>`
          : `<span style="color:#ef4444">${escapeHtml(r.status)}</span>`;

        return `
          <tr>
            <td><code>${r.addr_hex}</code></td>
            <td class="text-muted">${r.addr_dec}</td>
            <td>${statusBadge}</td>
            <td><code>${r.val_hex}</code></td>
            <td><strong>${isOk ? r.val_u16 : "-"}</strong></td>
            <td>${isOk ? r.val_i16 : "-"}</td>
            <td><strong class="text-cyan">${escapeHtml(r.name || "")}</strong></td>
            <td class="text-muted">${escapeHtml(r.desc || "")}</td>
          </tr>`;
      }).join("");
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center" style="color:#ef4444">${escapeHtml(tr("scanner.err", { msg: err.message }))}</td></tr>`;
    }
  }

  // -------------------------------------------------------------------------
  // Connection Actions
  // -------------------------------------------------------------------------
  async function fetchPorts() {
    try {
      const res = await fetch("/api/ports");
      const data = await res.json();
      const ports = data.ports || [];
      portSelect.innerHTML = "";

      if (ports.length === 0) {
        const opt = document.createElement("option");
        opt.value = "COM5";
        opt.textContent = tr("header.port_none");
        portSelect.appendChild(opt);
      } else {
        ports.forEach(p => {
          const opt = document.createElement("option");
          opt.value = p.device;
          opt.textContent = p.description ? `${p.device} [${p.description}]` : p.device;
          if (p.device === (data.current_port || "COM5")) opt.selected = true;
          portSelect.appendChild(opt);
        });
      }
    } catch (err) {
      console.warn("Failed to fetch ports:", err);
    }
  }

  async function connectInverter(port, simulated) {
    statusText.textContent = tr("header.status_connecting");
    try {
      const res = await fetch("/api/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: port, simulated: simulated })
      });
      const result = await res.json();
      if (!result.success && !simulated) {
        alert(tr("header.connect_fail", { port }));
      }
    } catch (err) {
      alert(tr("header.connect_err", { msg: err.message }));
    }
  }

  function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  // =========================================================================
  // Auto-Rescue Battery Protection Management
  // =========================================================================
  const rescueStatusPill = document.getElementById("rescueStatusPill");
  const rescueStatusText = document.getElementById("rescueStatusText");
  const chkRescueEnable = document.getElementById("chkRescueEnable");
  const rescueEnableLabel = document.getElementById("rescueEnableLabel");
  const inputRescueTrigger = document.getElementById("inputRescueTrigger");
  const inputRescueRecovery = document.getElementById("inputRescueRecovery");
  const btnSaveRescueConfig = document.getElementById("btnSaveRescueConfig");
  const chargeModePillGroup = document.getElementById("chargeModePillGroup");
  const currentChargePriorityDesc = document.getElementById("currentChargePriorityDesc");

  let rescueConfigLoaded = false;

  async function initAutoRescuePanel() {
    try {
      const res = await fetch("/api/rescue/config");
      if (res.ok) {
        const cfg = await res.json();
        if (chkRescueEnable) chkRescueEnable.checked = cfg.enabled ?? true;
        if (inputRescueTrigger) inputRescueTrigger.value = (cfg.trigger_voltage ?? 23.5).toFixed(1);
        if (inputRescueRecovery) inputRescueRecovery.value = (cfg.recovery_voltage ?? 24.6).toFixed(1);
        rescueConfigLoaded = true;
      }
    } catch (e) {
      console.warn("Failed to load rescue config:", e);
    }

    if (chkRescueEnable) {
      chkRescueEnable.addEventListener("change", async () => {
        const isEnabled = chkRescueEnable.checked;
        if (rescueEnableLabel) {
          rescueEnableLabel.textContent = tr(isEnabled ? "rescue.enable_on" : "rescue.enable_off");
        }
        await saveRescueSettings({ enabled: isEnabled });
      });
    }

    if (btnSaveRescueConfig) {
      btnSaveRescueConfig.addEventListener("click", async () => {
        const trigger = parseFloat(inputRescueTrigger.value) || 23.5;
        const recovery = parseFloat(inputRescueRecovery.value) || 24.6;
        if (trigger >= recovery) {
          alert(tr("rescue.invalid_thresh"));
          return;
        }
        const originalText = btnSaveRescueConfig.textContent;
        btnSaveRescueConfig.textContent = tr("rescue.saving");
        const success = await saveRescueSettings({
          trigger_voltage: trigger,
          recovery_voltage: recovery
        });
        if (success) {
          btnSaveRescueConfig.textContent = tr("rescue.saved");
          setTimeout(() => { btnSaveRescueConfig.textContent = tr("rescue.save_btn"); }, 1500);
        } else {
          btnSaveRescueConfig.textContent = tr("rescue.save_fail");
          setTimeout(() => { btnSaveRescueConfig.textContent = tr("rescue.save_btn"); }, 1500);
        }
      });
    }

    if (chargeModePillGroup) {
      chargeModePillGroup.querySelectorAll(".btn-charge-mode").forEach(btn => {
        btn.addEventListener("click", async () => {
          const code = parseInt(btn.getAttribute("data-code"));
          await sendChargePriorityChange(code);
        });
      });
    }
  }

  async function saveRescueSettings(patchObj) {
    try {
      const res = await fetch("/api/rescue/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patchObj)
      });
      if (res.ok) {
        const updated = await res.json();
        addTerminalLog("INFO", `Auto-Rescue updated: enabled=${updated.enabled}, trigger=${updated.trigger_voltage}V, recovery=${updated.recovery_voltage}V`);
        return true;
      }
    } catch (e) {
      console.error("Error saving rescue settings:", e);
    }
    return false;
  }

  async function sendChargePriorityChange(code) {
    const modeName = tr(`charge_prio.${code}`, null, `Code ${code}`);
    addTerminalLog("INFO", `Sending charge priority change: ${modeName}...`);
    try {
      const res = await fetch("/api/settings/charge_priority", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority_code: code })
      });
      const data = await res.json();
      if (data.success) {
        addTerminalLog("SUCCESS", `✅ Charge priority [06] changed to ${modeName}`);
        highlightActiveChargeMode(code);
      } else {
        alert(tr("rescue.chg_fail"));
      }
    } catch (e) {
      alert(tr("rescue.comm_err", { msg: e.message }));
    }
  }

  function highlightActiveChargeMode(activeCode) {
    if (!chargeModePillGroup) return;
    chargeModePillGroup.querySelectorAll(".btn-charge-mode").forEach(btn => {
      const btnCode = parseInt(btn.getAttribute("data-code"));
      btn.classList.remove("active", "mode-oso", "mode-cso");
      if (btnCode === activeCode) {
        btn.classList.add("active");
        if (activeCode === 3) btn.classList.add("mode-oso");
        if (activeCode === 0) btn.classList.add("mode-cso");
      }
    });

    if (currentChargePriorityDesc) {
      const prioLabel = tr(`charge_prio.${activeCode}`, null, `Code ${activeCode}`);
      currentChargePriorityDesc.textContent = tr("rescue.current_mode_fmt", { prio: prioLabel });
    }
  }

  function updateAutoRescueUI(rescueData, settingsData) {
    if (!rescueData) return;

    // 1. Sync toggle if not user-focused
    if (chkRescueEnable && rescueConfigLoaded) {
      if (rescueEnableLabel) {
        rescueEnableLabel.textContent = tr(rescueData.enabled ? "rescue.enable_on" : "rescue.enable_off");
      }
    }

    // 2. Status Pill
    if (rescueStatusPill) {
      rescueStatusPill.className = "rescue-status-pill";
      if (!rescueData.enabled) {
        rescueStatusPill.classList.add("status-disabled");
      } else if (rescueData.state === "RESCUING") {
        rescueStatusPill.classList.add("status-rescuing");
      } else {
        rescueStatusPill.classList.add("status-idle");
      }
    }

    if (rescueStatusText) {
      const trig = rescueData.trigger_voltage ?? 23.5;
      const rec = rescueData.recovery_voltage ?? 24.6;
      const rMode = rescueData.mode || "pc";
      let txt = "";
      if (!rescueData.enabled) {
        txt = tr("rescue.status_off");
      } else if (rMode === "esp32") {
        txt = rescueData.state === "RESCUING"
          ? tr("rescue.status_esp_active", { rec })
          : tr("rescue.status_esp_wait", { trig });
        if (!rescueData.esp32_synced) txt += tr("rescue.esp_unsynced");
      } else {
        const modeLabel = rMode === "simulated" ? "Demo" : "RS485";
        txt = rescueData.state === "RESCUING"
          ? tr("rescue.status_pc_active", { mode: modeLabel, rec })
          : tr("rescue.status_pc_wait", { mode: modeLabel, trig });
      }
      if (rescueData.pending_action) {
        const sec = Math.max(0, Math.round(10 - rescueData.pending_seconds));
        txt += ` (${sec}s...)`;
      }
      rescueStatusText.textContent = txt;
    }

    // 3. Highlight current active charge mode pill
    if (settingsData && settingsData.charge_priority_code !== undefined) {
      highlightActiveChargeMode(settingsData.charge_priority_code);
    }
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
});
