// -*- coding: utf-8 -*-
/**
 * Lightweight Internationalization (i18n) Engine for HSImonitor.
 * Loads JSON dictionaries from /static/locales/<lang>.json with English ("en") fallback.
 */
(function (global) {
  const STORAGE_KEY = "hsi_lang";
  const SUPPORTED_LANGS = ["en", "ja"];

  let currentLang = "en";
  let dictEn = {};
  let dictCurrent = {};
  const listeners = [];

  function detectInitialLang() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && SUPPORTED_LANGS.includes(saved)) {
        return saved;
      }
    } catch (_) {}
    const nav = (navigator.language || navigator.userLanguage || "en").toLowerCase();
    return nav.startsWith("ja") ? "ja" : "en";
  }

  async function fetchLocale(lang) {
    try {
      const res = await fetch(`/static/locales/${lang}.json?v=1.0`, { cache: "no-cache" });
      if (!res.ok) return {};
      return await res.json();
    } catch (e) {
      console.warn(`[i18n] Failed to load locale '${lang}':`, e);
      return {};
    }
  }

  function interpolate(template, params) {
    if (!params || typeof template !== "string") return template;
    return template.replace(/\{(\w+)\}/g, (_, k) =>
      params[k] !== undefined && params[k] !== null ? String(params[k]) : `{${k}}`
    );
  }

  function t(key, params, fallbackText) {
    let raw = dictCurrent[key];
    if (raw === undefined) raw = dictEn[key];
    if (raw === undefined) return fallbackText !== undefined ? fallbackText : key;
    return interpolate(raw, params);
  }

  function applyI18n(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key, null, el.textContent);
    });
    root.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.getAttribute("data-i18n-html");
      if (key) el.innerHTML = t(key, null, el.innerHTML);
    });
    root.querySelectorAll("[data-i18n-title]").forEach((el) => {
      const key = el.getAttribute("data-i18n-title");
      if (key) el.setAttribute("title", t(key, null, el.getAttribute("title") || ""));
    });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.setAttribute("placeholder", t(key, null, el.getAttribute("placeholder") || ""));
    });
    root.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      const key = el.getAttribute("data-i18n-aria");
      if (key) el.setAttribute("aria-label", t(key, null, el.getAttribute("aria-label") || ""));
    });
    document.documentElement.lang = currentLang;
  }

  async function setLang(lang) {
    if (!SUPPORTED_LANGS.includes(lang)) lang = "en";
    currentLang = lang;
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch (_) {}

    if (lang === "en") {
      if (Object.keys(dictEn).length === 0) dictEn = await fetchLocale("en");
      dictCurrent = dictEn;
    } else {
      dictCurrent = await fetchLocale(lang);
    }

    const sel = document.getElementById("langSelect");
    if (sel && sel.value !== lang) sel.value = lang;

    applyI18n(document);
    listeners.forEach((fn) => {
      try { fn(currentLang); } catch (e) { console.error(e); }
    });
  }

  function onLangChange(fn) {
    if (typeof fn === "function") listeners.push(fn);
  }

  const ready = (async () => {
    currentLang = detectInitialLang();
    dictEn = await fetchLocale("en");
    if (currentLang === "en") {
      dictCurrent = dictEn;
    } else {
      dictCurrent = await fetchLocale(currentLang);
    }
    return currentLang;
  })();

  global.I18N = {
    t,
    setLang,
    getLang: () => currentLang,
    applyI18n,
    onLangChange,
    ready,
    SUPPORTED_LANGS,
  };
})(window);

