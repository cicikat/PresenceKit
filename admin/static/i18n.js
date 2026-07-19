(function () {
  'use strict';

  const DEFAULT_LANGUAGE = 'zh-CN';
  const STORAGE_KEY = 'presence.admin.language';
  const SUPPORTED_LANGUAGES = new Set(['zh-CN', 'en']);

  const I18N = {
    'zh-CN': {
      'app.title': 'His Presence 管理面板',
      'auth.title': '🤖 管理面板',
      'auth.subtitle': 'His Presence · 输入管理密钥登录',
      'auth.login': '登录',
      'common.language': '语言',
      'nav.console': '运维控制台',
      'nav.setup': '配置',
      'nav.group.creation': '🎨 创作',
      'nav.character': '角色卡',
      'nav.lorebook': '现实设定',
      'nav.dream_settings': '梦境设定',
      'nav.group.operations': '🛠 运维',
      'nav.status': '系统状态',
      'nav.scheduler': '调度器',
      'nav.users': '用户管理',
      'nav.logs': '错误日志',
      'nav.auth_tokens': 'Token',
      'nav.model_routing': '模型路由',
      'nav.relationship_facts': '关系事实',
      'nav.group.internal_state': '🔬 内部状态',
      'nav.mood': '情绪·花园',
      'nav.dream_state': '梦境状态',
      'nav.memory': '记忆探查',
      'nav.hidden_state': '隐性状态',
      'nav.chat_log': '聊天日志',
      'nav.runtime': '运行时内部态',
      'nav.group.observation': '🔍 观测',
      'nav.growth': '成长',
      'nav.visual': '视觉',
      'nav.spend': '支出',
      'nav.group_arbiter': '群聊仲裁',
      'nav.memory_summary': '记忆摘要',
      'nav.prompt_layers': 'Prompt 层检视',
      'nav.probe': '探针观测',
      'nav.dream_prompt': '梦境 Prompt',
      'nav.trigger_catalog': '触发器目录',
      'nav.vector_store': '向量库',
      'nav.provenance': '印象溯源',
      'nav.pet': '宠物',
      'nav.chat_with': '与',
      'common.refresh': '刷新',
      'common.save': '保存',
      'common.saved': '已保存',
      'common.loading': '加载中…',
      'common.enabled': '已启用',
      'common.disabled': '未启用',
      'status.title': '系统状态',
      'status.known_users': '已知用户',
      'status.quick_actions': '快捷操作',
      'status.reload_all': '⚡ 热重载全部配置',
      'status.feature_switches': 'Feature switches',
      'status.save_reload': '保存并热重载',
      'status.registered_tools': '已注册工具',
      'status.read_only': '只读',
      'status.proxy.title': '代理配置',
      'status.proxy.hint': '保存后热重载；仅后端出站请求使用代理，桌面客户端 HTTP 始终禁用代理。',
      'status.proxy.enable': '启用代理',
      'status.proxy.http': 'HTTP 代理地址',
      'status.proxy.https': 'HTTPS 代理地址',
      'status.context.title': '上下文长度',
      'status.context.hint': '保存后热重载，影响后续新请求使用的上下文窗口。',
      'status.context.max_rounds': '最大轮数（每轮 = 一问一答）',
      'status.context.rounds': '轮',
      'status.llm.title': 'LLM 生成参数',
      'status.llm.hint': 'legacy 参数可在此热更新；启用多模型后优先在「模型路由」编辑 preset 参数。',
      'status.llm.max_tokens': '单次回复最大 token 数',
      'status.llm.recommended': '推荐范围：',
      'status.llm.immersive': '💬 沉浸对话  500–800',
      'status.llm.roleplay': '🎭 角色扮演  1500–3000',
      'status.llm.temperature': '值越高回复越随机，越低越保守',
      'status.llm.top_p': '核采样概率，配合 temperature 控制多样性',
      'status.llm.frequency_penalty': '惩罚重复词，值越高越不容易重复用词',
      'status.vision.title': '视觉模型配置（图片识别）',
      'status.vision.enable': '启用',
      'status.vision.key_hint': 'API Key 留空时保留已有密钥；保存后热重载。',
      'status.vision.model': '模型',
      'status.vision.custom': '自定义',
      'status.vision.key_placeholder': '留空则保留已有 API Key',
      'status.vision.base_hint': '（自动填写，自定义时可修改）',
      'status.vision.gemini_before': '免费，前往',
      'status.vision.gemini_after': '申请 API Key，推荐模型 gemini-2.0-flash',
      'status.vision.glm_before': '免费，前往',
      'status.vision.glm_after': '申请，推荐模型 glm-4v-flash',
      'status.vision.openai_help': '付费，需要信用卡，推荐模型 gpt-4o-mini',
      'status.vision.custom_help': '手动填写 Base URL 和模型名',
      'status.screen.title': '屏幕内容查看',
      'status.screen.cooldown': '冷却分钟数',
      'status.relay.title': '中继唤醒',
      'status.relay.token': 'Token（留空保留已有值）',
      'status.tts.title': 'TTS 配置',
      'status.tts.emotion': '情绪分档',
      'status.tts.desktop': '桌面语音条',
      'status.tts.ref_audio': '参考音频路径',
      'status.tts.ref_text': '参考文本',
      'status.tts.ref_placeholder': '参考音频对应文本',
      'status.tts.speed': '语速',
      'status.pronoun.title': '用户称谓',
      'status.pronoun.user': '用户',
      'status.pronoun.value': '称谓',
      'status.pronoun.she': '她',
      'status.pronoun.he': '他',
      'status.pronoun.they': 'TA',
      'status.pronoun.it': '它',
      'status.tools.empty': '无已注册工具',
      'status.tools.name': '工具',
      'status.tools.description': '描述',
      'status.error.load': '获取状态失败: {error}',
      'status.error.reload': '热重载失败: {error}',
      'status.context.load_error': '读取上下文配置失败: {error}',
      'status.context.saved': '上下文已设为 {count} 轮',
      'status.llm.load_error': '加载 LLM 参数失败',
      'status.llm.saved': 'LLM 参数已保存',
      'status.vision.manual_input': '手动输入',
      'status.proxy.load_error': '读取代理配置失败: {error}',
      'status.proxy.saved': '代理配置已保存并热重载',
      'status.tools.load_error': '加载失败：{error}',
      'status.screen.load_error': '读取屏幕内容查看配置失败: {error}',
      'status.screen.saved': '屏幕内容查看配置已保存',
      'status.tts.load_error': '读取 TTS 配置失败: {error}',
      'status.tts.saved': 'TTS 配置已保存',
      'status.pronoun.load_error': '读取称谓失败: {error}',
      'status.pronoun.select_user': '请先选择用户',
      'status.pronoun.saved': '称谓已保存',
      'status.relay.configured': '已配置（{value}），留空保留',
      'status.relay.unconfigured': '未配置',
      'status.relay.load_error': '读取中继失败: {error}',
      'status.relay.saved': '中继配置已保存',
      'common.save_failed': '保存失败: {error}',
      'flag.qq': 'QQ 通道',
      'flag.mail': '邮件通道',
      'flag.visual_perception': '视觉感知',
      'flag.spend': '支出意向',
      'flag.practice': '自主练习',
      'flag.action_trace': '行为痕迹',
      'flag.intent_reflex': '意图反射（降级路径）',
      'flag.mcp_servers': 'MCP 外部工具',
      'flag.fs_access': '文件只读访问',
      'flag.anti_collapse': '输出防坍缩',
      'flag.coplay': '陪玩部署',
      'flag.toy_autogrow': '玩具自主生长',
      'flag.web_autosearch': '自主联网搜索',
      'flag.performance_mapping': '表演标注映射',
      'flag.private_exchange': '角色私下往来',
    },
    en: {
      'app.title': 'His Presence Admin Panel',
      'auth.title': '🤖 Admin Panel',
      'auth.subtitle': 'His Presence · Enter the admin key to sign in',
      'auth.login': 'Sign in',
      'common.language': 'Language',
      'nav.console': 'Operations Console',
      'nav.setup': 'Setup',
      'nav.group.creation': '🎨 Creation',
      'nav.character': 'Characters',
      'nav.lorebook': 'Reality Lore',
      'nav.dream_settings': 'Dream Settings',
      'nav.group.operations': '🛠 Operations',
      'nav.status': 'System Status',
      'nav.scheduler': 'Scheduler',
      'nav.users': 'Users',
      'nav.logs': 'Error Logs',
      'nav.auth_tokens': 'Tokens',
      'nav.model_routing': 'Model Routing',
      'nav.relationship_facts': 'Relationship Facts',
      'nav.group.internal_state': '🔬 Internal State',
      'nav.mood': 'Mood & Garden',
      'nav.dream_state': 'Dream State',
      'nav.memory': 'Memory Explorer',
      'nav.hidden_state': 'Hidden State',
      'nav.chat_log': 'Chat Logs',
      'nav.runtime': 'Runtime Internals',
      'nav.group.observation': '🔍 Observation',
      'nav.growth': 'Growth',
      'nav.visual': 'Vision',
      'nav.spend': 'Spending',
      'nav.group_arbiter': 'Group Arbiter',
      'nav.memory_summary': 'Memory Summary',
      'nav.prompt_layers': 'Prompt Layers',
      'nav.probe': 'Probe Inspector',
      'nav.dream_prompt': 'Dream Prompt',
      'nav.trigger_catalog': 'Trigger Catalog',
      'nav.vector_store': 'Vector Store',
      'nav.provenance': 'Impression Provenance',
      'nav.pet': 'Pet',
      'nav.chat_with': 'Chat with',
      'common.refresh': 'Refresh',
      'common.save': 'Save',
      'common.saved': 'Saved',
      'common.loading': 'Loading…',
      'common.enabled': 'Enabled',
      'common.disabled': 'Disabled',
      'status.title': 'System Status',
      'status.known_users': 'Known Users',
      'status.quick_actions': 'Quick Actions',
      'status.reload_all': '⚡ Reload All Configuration',
      'status.feature_switches': 'Feature Switches',
      'status.save_reload': 'Save and Reload',
      'status.registered_tools': 'Registered Tools',
      'status.read_only': 'Read-only',
      'status.proxy.title': 'Proxy Configuration',
      'status.proxy.hint': 'Changes reload immediately. The proxy is only used for backend outbound requests; desktop HTTP always bypasses it.',
      'status.proxy.enable': 'Enable proxy',
      'status.proxy.http': 'HTTP proxy URL',
      'status.proxy.https': 'HTTPS proxy URL',
      'status.context.title': 'Context Length',
      'status.context.hint': 'Changes reload immediately and affect the context window of subsequent requests.',
      'status.context.max_rounds': 'Maximum rounds (one round = one question and answer)',
      'status.context.rounds': 'rounds',
      'status.llm.title': 'LLM Generation Parameters',
      'status.llm.hint': 'Legacy parameters can be updated here. With multi-model routing enabled, edit preset parameters under Model Routing.',
      'status.llm.max_tokens': 'Maximum tokens per response',
      'status.llm.recommended': 'Recommended ranges:',
      'status.llm.immersive': '💬 Immersive chat  500–800',
      'status.llm.roleplay': '🎭 Roleplay  1500–3000',
      'status.llm.temperature': 'Higher values are more random; lower values are more conservative',
      'status.llm.top_p': 'Nucleus sampling probability; use with temperature to control diversity',
      'status.llm.frequency_penalty': 'Penalizes repeated words; higher values reduce repetition',
      'status.vision.title': 'Vision Model (Image Recognition)',
      'status.vision.enable': 'Enable',
      'status.vision.key_hint': 'Leave API Key blank to keep the existing secret. Changes reload immediately.',
      'status.vision.model': 'Model',
      'status.vision.custom': 'Custom',
      'status.vision.key_placeholder': 'Leave blank to keep the existing API Key',
      'status.vision.base_hint': '(filled automatically; editable for Custom)',
      'status.vision.gemini_before': 'Free. Get an API Key at',
      'status.vision.gemini_after': '; gemini-2.0-flash is recommended',
      'status.vision.glm_before': 'Free. Apply at',
      'status.vision.glm_after': '; glm-4v-flash is recommended',
      'status.vision.openai_help': 'Paid and requires a credit card; gpt-4o-mini is recommended',
      'status.vision.custom_help': 'Enter the Base URL and model name manually',
      'status.screen.title': 'Screen Content Access',
      'status.screen.cooldown': 'Cooldown in minutes',
      'status.relay.title': 'Relay Wake',
      'status.relay.token': 'Token (leave blank to keep the existing value)',
      'status.tts.title': 'TTS Configuration',
      'status.tts.emotion': 'Emotion profiles',
      'status.tts.desktop': 'Desktop voice bar',
      'status.tts.ref_audio': 'Reference audio path',
      'status.tts.ref_text': 'Reference text',
      'status.tts.ref_placeholder': 'Text matching the reference audio',
      'status.tts.speed': 'Speed',
      'status.pronoun.title': 'User Pronoun',
      'status.pronoun.user': 'User',
      'status.pronoun.value': 'Pronoun',
      'status.pronoun.she': 'She',
      'status.pronoun.he': 'He',
      'status.pronoun.they': 'They',
      'status.pronoun.it': 'It',
      'status.tools.empty': 'No registered tools',
      'status.tools.name': 'Tool',
      'status.tools.description': 'Description',
      'status.error.load': 'Failed to load status: {error}',
      'status.error.reload': 'Reload failed: {error}',
      'status.context.load_error': 'Failed to load context configuration: {error}',
      'status.context.saved': 'Context set to {count} rounds',
      'status.llm.load_error': 'Failed to load LLM parameters',
      'status.llm.saved': 'LLM parameters saved',
      'status.vision.manual_input': 'Enter manually',
      'status.proxy.load_error': 'Failed to load proxy configuration: {error}',
      'status.proxy.saved': 'Proxy configuration saved and reloaded',
      'status.tools.load_error': 'Failed to load: {error}',
      'status.screen.load_error': 'Failed to load screen-content settings: {error}',
      'status.screen.saved': 'Screen-content settings saved',
      'status.tts.load_error': 'Failed to load TTS configuration: {error}',
      'status.tts.saved': 'TTS configuration saved',
      'status.pronoun.load_error': 'Failed to load pronoun: {error}',
      'status.pronoun.select_user': 'Select a user first',
      'status.pronoun.saved': 'Pronoun saved',
      'status.relay.configured': 'Configured ({value}); leave blank to keep it',
      'status.relay.unconfigured': 'Not configured',
      'status.relay.load_error': 'Failed to load relay settings: {error}',
      'status.relay.saved': 'Relay configuration saved',
      'common.save_failed': 'Save failed: {error}',
      'flag.qq': 'QQ Channel',
      'flag.mail': 'Email Channel',
      'flag.visual_perception': 'Visual Perception',
      'flag.spend': 'Spending Intent',
      'flag.practice': 'Autonomous Practice',
      'flag.action_trace': 'Action Trace',
      'flag.intent_reflex': 'Intent Reflex (Fallback)',
      'flag.mcp_servers': 'MCP External Tools',
      'flag.fs_access': 'Read-only File Access',
      'flag.anti_collapse': 'Output Anti-collapse',
      'flag.coplay': 'Co-play Deployment',
      'flag.toy_autogrow': 'Toy Autogrow',
      'flag.web_autosearch': 'Autonomous Web Search',
      'flag.performance_mapping': 'Performance Mapping',
      'flag.private_exchange': 'Private Character Exchanges',
    },
  };

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

  function applyI18n(root) {
    const scope = root || document;
    document.documentElement.lang = currentLanguage;
    document.title = t('app.title', document.title);
    scope.querySelectorAll('[data-i18n]').forEach(element => {
      const key = element.dataset.i18n;
      const fallback = element.dataset.i18nFallback || element.textContent;
      element.textContent = t(key, fallback);
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(element => {
      const key = element.dataset.i18nPlaceholder;
      element.placeholder = t(key, element.placeholder);
    });
    const selector = document.getElementById('admin-language-select');
    if (selector) selector.value = currentLanguage;
  }

  function setLanguage(language) {
    if (!SUPPORTED_LANGUAGES.has(language) || language === currentLanguage) return;
    currentLanguage = language;
    try { localStorage.setItem(STORAGE_KEY, language); } catch (_error) { /* best effort */ }
    applyI18n();
    window.dispatchEvent(new CustomEvent('admin-language-changed', {detail: {language}}));
  }

  function getLanguage() {
    return currentLanguage;
  }

  window.AdminI18n = {I18N, applyI18n, getLanguage, setLanguage, t};
  window.t = t;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => applyI18n(), {once: true});
  } else {
    applyI18n();
  }
})();
