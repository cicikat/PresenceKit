(function () {
  'use strict';

  const DEFAULT_LANGUAGE = 'zh-CN';
  const STORAGE_KEY = 'presence.admin.language';
  const SUPPORTED_LANGUAGES = new Set(['zh-CN', 'en']);

  const I18N = window.AdminI18nMessages;

  function readLanguage() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return SUPPORTED_LANGUAGES.has(saved) ? saved : DEFAULT_LANGUAGE;
    } catch (_error) {
      return DEFAULT_LANGUAGE;
    }
  }

  let currentLanguage = readLanguage();

  function format(template, params) {
    if (!params) return template;
    return String(template).replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : `{${name}}`
    );
  }

  function t(key, fallback, params) {
    const active = I18N[currentLanguage] || {};
    if (Object.prototype.hasOwnProperty.call(active, key)) return format(active[key], params);
    console.debug(`[admin-i18n] missing ${currentLanguage}: ${key}`);
    const chinese = I18N[DEFAULT_LANGUAGE] || {};
    const value = Object.prototype.hasOwnProperty.call(chinese, key) ? chinese[key] : fallback;
    return format(value == null ? key : value, params);
  }

  const originalText = new WeakMap();
  const originalAttributes = new WeakMap();
  const legacyExact = new Map();
  const legacyEnglishExact = new Map();
  const legacyPatterns = [];
  const legacyFragments = [];

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  Object.entries(I18N[DEFAULT_LANGUAGE]).forEach(([key, chinese]) => {
    const english = I18N.en[key];
    if (typeof chinese !== 'string' || typeof english !== 'string' || chinese === english) return;
    if (key.startsWith('fragment.')) {
      legacyFragments.push([chinese, english]);
      return;
    }
    legacyExact.set(chinese.trim(), english);
    legacyEnglishExact.set(english.trim(), chinese);
    const names = [];
    let cursor = 0;
    let source = '';
    const matcher = /\{([a-zA-Z0-9_]+)\}/g;
    let match;
    while ((match = matcher.exec(chinese)) !== null) {
      source += escapeRegExp(chinese.slice(cursor, match.index)) + '(.*?)';
      names.push(match[1]);
      cursor = match.index + match[0].length;
    }
    if (names.length) {
      source += escapeRegExp(chinese.slice(cursor));
      const literalLength = chinese.replace(/\{[a-zA-Z0-9_]+\}/g, '').length;
      legacyPatterns.push({regex: new RegExp(`^${source}$`, 's'), names, english, chinese, literalLength});
    }
  });

  legacyPatterns.sort((a, b) => b.literalLength - a.literalLength || a.names.length - b.names.length);
  legacyFragments.sort((a, b) => b[0].length - a[0].length);

  function translateUiText(value, allowFragments=false) {
    const raw = String(value == null ? '' : value);
    if (currentLanguage === DEFAULT_LANGUAGE || !/[\u3400-\u9fff]/.test(raw)) return raw;
    const leading = raw.match(/^\s*/)?.[0] || '';
    const trailing = raw.match(/\s*$/)?.[0] || '';
    const end = raw.length - trailing.length;
    const core = raw.slice(leading.length, end > leading.length ? end : raw.length);
    const exact = legacyExact.get(core);
    if (exact != null) return `${leading}${exact}${trailing}`;
    for (const item of legacyPatterns) {
      const match = item.regex.exec(core);
      if (!match) continue;
      const params = {};
      item.names.forEach((name, index) => { params[name] = match[index + 1]; });
      return `${leading}${format(item.english, params)}${trailing}`;
    }
    if (allowFragments) {
      let translated = core;
      legacyFragments.forEach(([chinese, english]) => { translated = translated.split(chinese).join(english); });
      if (translated !== core) return `${leading}${translated}${trailing}`;
    }
    return raw;
  }

  function canonicalChinese(value) {
    const raw = String(value == null ? '' : value);
    if (/[\u3400-\u9fff]/.test(raw)) return raw;
    const leading = raw.match(/^\s*/)?.[0] || '';
    const trailing = raw.match(/\s*$/)?.[0] || '';
    const end = raw.length - trailing.length;
    const core = raw.slice(leading.length, end > leading.length ? end : raw.length);
    const exact = legacyEnglishExact.get(core);
    if (exact != null) return `${leading}${exact}${trailing}`;
    // Parameterized English can be a model name or technical label (Tool Loop).
    // Only explicit translation keys and exact phrases may reverse-translate it.
    return raw;
  }

  function legacyExcluded(element) {
    return !element || !!element.closest(
      'script, style, code, pre, .log-box, .i18n-raw, #chat-messages, [data-i18n], [data-i18n-skip]'
    );
  }

  function applyLegacyTextNode(node) {
    const parent = node.parentElement;
    if (!parent || legacyExcluded(parent)) return;
    if (!originalText.has(node)) originalText.set(node, canonicalChinese(node.nodeValue));
    const original = originalText.get(node);
    const translated = currentLanguage === DEFAULT_LANGUAGE ? original : translateUiText(original);
    if (node.nodeValue !== translated) node.nodeValue = translated;
  }

  function applyLegacyElement(element) {
    if (!element || element.nodeType !== 1 || legacyExcluded(element)) return;
    Array.from(element.childNodes || []).forEach(node => {
      if (node.nodeType === 3) applyLegacyTextNode(node);
    });
    const saved = originalAttributes.get(element) || {};
    ['placeholder', 'title', 'aria-label'].forEach(attribute => {
      if (!element.hasAttribute(attribute) || element.hasAttribute(`data-i18n-${attribute}`)) return;
      if (!Object.prototype.hasOwnProperty.call(saved, attribute)) saved[attribute] = canonicalChinese(element.getAttribute(attribute));
      const original = saved[attribute];
      element.setAttribute(attribute, currentLanguage === DEFAULT_LANGUAGE ? original : translateUiText(original));
    });
    originalAttributes.set(element, saved);
  }

  function applyLegacyI18n(root) {
    const scope = root && root.nodeType ? root : document;
    if (scope.nodeType === 3) {
      applyLegacyTextNode(scope);
      return;
    }
    if (scope.nodeType === 1) applyLegacyElement(scope);
    const elements = scope.querySelectorAll ? scope.querySelectorAll('*') : [];
    elements.forEach(applyLegacyElement);
  }

  function applyI18n(root) {
    const scope = root || document;
    document.documentElement.lang = currentLanguage;
    document.title = t('app.title', document.title);
    scope.querySelectorAll('[data-i18n]').forEach(element => {
      const key = element.dataset.i18n;
      const fallback = element.dataset.i18nFallback || element.textContent;
      const value = t(key, fallback);
      if (element.textContent !== value) element.textContent = value;
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
      const key = element.dataset.i18nPlaceholder;
      element.placeholder = t(key, element.placeholder);
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(element => {
      const key = element.dataset.i18nTitle;
      element.setAttribute('title', t(key, element.getAttribute('title')));
    });
    scope.querySelectorAll('[data-i18n-alt]').forEach(element => {
      const key = element.dataset.i18nAlt;
      element.setAttribute('alt', t(key, element.getAttribute('alt')));
    });
    scope.querySelectorAll('[data-i18n-aria-label]').forEach(element => {
      const key = element.dataset.i18nAriaLabel;
      element.setAttribute('aria-label', t(key, element.getAttribute('aria-label')));
    });
    applyLegacyI18n(scope);
    document.querySelectorAll('[data-language-select]').forEach(selector => {
      selector.value = currentLanguage;
      selector.title = t('common.language_scope');
    });
  }

  async function setLanguage(language, {persistPrompt = true} = {}) {
    if (!SUPPORTED_LANGUAGES.has(language) || language === currentLanguage) return;
    if (persistPrompt && window.saveAdminPromptLanguage) {
      try {
        await window.saveAdminPromptLanguage(language);
      } catch (error) {
        applyI18n();
        window.toast?.(t('common.save_failed', '保存失败: {error}', {error: error.message}), 'err');
        return;
      }
    }
    currentLanguage = language;
    try { localStorage.setItem(STORAGE_KEY, language); } catch (_error) { /* best effort */ }
    applyI18n();
    window.dispatchEvent(new CustomEvent('admin-language-changed', {detail: {language}}));
  }

  function getLanguage() {
    return currentLanguage;
  }

  window.AdminI18n = {I18N, applyI18n, applyLegacyI18n, getLanguage, setLanguage, t, translateUiText};
  window.t = t;

  const nativePrompt = window.prompt ? window.prompt.bind(window) : null;
  const nativeConfirm = window.confirm ? window.confirm.bind(window) : null;
  const nativeAlert = window.alert ? window.alert.bind(window) : null;
  if (nativePrompt) window.prompt = (message, defaultValue) => nativePrompt(translateUiText(message, true), defaultValue);
  if (nativeConfirm) window.confirm = message => nativeConfirm(translateUiText(message, true));
  if (nativeAlert) window.alert = message => nativeAlert(translateUiText(message, true));

  if (typeof MutationObserver !== 'undefined') {
    const observer = new MutationObserver(records => {
      records.forEach(record => {
        if (record.type === 'characterData') applyLegacyTextNode(record.target);
        record.addedNodes?.forEach(applyLegacyI18n);
      });
    });
    const startObserver = () => observer.observe(document.documentElement, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    if (document.documentElement) startObserver();
    else document.addEventListener('DOMContentLoaded', startObserver, {once: true});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => applyI18n(), {once: true});
  } else {
    applyI18n();
  }
})();
