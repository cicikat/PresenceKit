// Approved operator copy. Keep this projection when removing temporary editing controls.
window.AdminCopyDefaults = {
  "text": {
    "i18n:nav.page.tools": "工具状态",
    "i18n:nav.page.mcp": "MCP",
    "navgroup-features:消息分发与客户端设置": "客户端设置",
    "page-client-settings:消息分发与客户端设置": "客户端设置",
    "i18n:nav.page.data-management": "单角色数据管理",
    "page-observe-vector:印象从哪里来": "概要记忆",
    "i18n:nav.page.observe-dream": "json观测总览",
    "page-setup:配置来源：本页保存基础连接；其余模型与路由的当前生效值请到模型路由页查看。": " ",
    "i18n:setup.base.description": "",
    "i18n:runtime_config.source_global": " ",
    "i18n:runtime_config.apply_reload": " ",
    "i18n:settings_center.global_defaults_execution_permissions_and_effective_availability_are_shown_separately_disabling_mult": " ",
    "i18n:mcp.security_notice": " ",
    "i18n:mcp.metadata_notice": "",
    "page-mcp:连接 MCP 服务并管理其工具策略。": "",
    "i18n:mcp.headers_label": " ",
    "i18n:mcp.policy.help": " ",
    "mcp-servers:严格模式：空白名单表示不授权任何工具。远端分类不授予权限，执行和确认由本地 policy 控制。": " ",
    "i18n:mcp.console.notice": " ",
    "mcp-console-tool-info:effect: read\ndomains: unclassified · interaction: unknown": " ",
    "i18n:autonomy.prerequisites": " ",
    "autonomy-tools-card:角色主动活动（没人跟她说话时）只能用这里打开的工具；聊天时的工具不受影响。一键开启只包含内置、无危险副作用的工具，MCP 工具需要单独确认。": " ",
    "i18n:agent_runtime_browser.description": " ",
    "i18n:status.dangermode.help": " ",
    "page-character-behavior:默认关闭。关闭后停止游戏会话，普通聊天保持原有行为。保存任何配置变化会关闭当前会话，需重新开始。": "默认关闭。关闭后停止游戏会话，普通聊天保持原有行为。保存任何配置变化会关闭当前会话，需重新开始。",
    "page-runtime-config:编辑代理、上下文、生成参数、Relay 和运行时安全闸。": "",
    "i18n:runtime_config.source_hint": " ",
    "i18n:runtime_config.reload_hint": " ",
    "page-tools:绿色表示该项运行判断通过，红色表示关闭或阻断；— 表示无数据或不适用。模型路由通过不等于上游连通，实际调用见调用记录。": " ",
    "i18n:food.hint": " ",
    "i18n:status.context.hint": " ",
    "i18n:runtime_config.source_legacy": " ",
    "page-conversation-settings:temperature 值越高回复越随机，越低越保守: 0.8 · top_p 核采样概率，配合 temperature 控制多样性: 0.9": " ",
    "page-conversation-settings:saveLlmParams": " ",
    "i18n:status.llm.hint": " ",
    "conversation-thinking-jump:思考总开关、方式和心声已移到「模型连接与分工」。桌面客户端只负责展开显示。": " ",
    "conversation-form-3:saveConversationSection": " ",
    "conversation-form-3:展开填写具体参数": " ",
    "i18n:life.hint": " ",
    "life-status:同步已开启 · 手填内容与图片描述分开保存": " ",
    "life-status:角色可读取生活记录。上传识别完成后，未读资料会随下一次对话或主动机会提供；已读不代表已经回复。": " ",
    "page-client-settings:只开启你使用的客户端。电脑、手机、QQ、微信开关保存后自动热生效；桌面/手机关闭后停止该入口收发，保留已有消息。QQ 关闭即断开连接，重新开启后自动连接。": " ",
    "page-client-settings:两项默认关闭，保存后热生效。群聊开启后仅响应 @ 机器人；其他用户开关控制非主用户的私聊。主用户私聊继续沿用现有链路。开启不代表群聊或多用户状态已完全隔离，设计确定后再开放。": "1",
    "mr-thinking-card:心声引导：已启用 · 随主生成发送。风格约 24 小时轮换；情绪沿用现有平滑状态。原生摘要是否遵从由模型决定，通用提示可能影响回复措辞。": " ",
    "i18n:character.section.basic_hint": " ",
    "i18n:character.section.persona_hint": " ",
    "i18n:character.section.world_book_hint": " ",
    "i18n:character.section.post_history_hint": " ",
    "i18n:character.dream_behavior.hint": " ",
    "i18n:runtime_config.pronoun_hint": " ",
    "i18n:dream.scenario_injection.hint": " ",
    "page-dream-settings:与入梦时选择的 dream_mode 相互独立，这里只决定本页显示的创作栏": " ",
    "i18n:dream.world.hint": " ",
    "i18n:dream.preset.independent_hint": " ",
    "i18n:settings_center.reality_assets_hint": " ",
    "user-data-content:通用池与角色 pack 分开展示，并保留 emotion 作用域。": " ",
    "ime-observation:判定后仍由角色决定是否开口，queued 只表示进入主动候选。最终发送结果见主动性观测，source 为 ime。": " ",
    "i18n:ime.observation.hint": " ",
    "page-observe-prompt:只读观测：数据来自最近对话快照，不会把观测结果写回配置或记忆。": " ",
    "i18n:observe.dream_prompt.ablation_hint": " ",
    "obs-ablation-card:只过滤注入，不短路检索；保存后下一轮对话起作用": " ",
    "i18n:overview.effective.description": " ",
    "resource-completeness-content:已知功能缺口（人工记录，随实现推进摘除）": " ",
    "resource-completeness-content:后端 /character/{id}/asset-bindings 已提供 live2d_model/model_3d 透传字段，前端尚未接入据此切换模型文件的实际逻辑（后端只负责存/传，不解析）。": " ",
    "resource-completeness-content:角色资产绑定(Live2D/3D)的前端消费": " ",
    "i18n:autonomy.tools.hint": " ",
    "i18n:autonomy.funnel.hint": " ",
    "i18n:cleanup.intro": " ",
    "i18n:logs.subtitle": " ",
    "i18n:observe.api_contract.subtitle": " ",
    "i18n:observe.api_contract.client_hint": " "
  },
  "order": {
    "navgroup-features": [
      "feature-center",
      "mcp",
      "autonomy-settings",
      "character-behavior",
      "role-bindings",
      "agent-runtime-browser",
      "device-policy",
      "output-settings",
      "coplay-config",
      "minecraft",
      "runtime-config",
      "tools",
      "conversation-settings",
      "service-center"
    ],
    "navgroup-services": [
      "client-settings",
      "model-routing",
      "tts-config",
      "local-model-runtime",
      "listening-player",
      "embedding-config",
      "network-config",
      "mail-config",
      "diary-config"
    ],
    "navgroup-creation": [
      "character",
      "lorebook",
      "dream-settings",
      "data-management",
      "observe-prompt",
      "relationship-facts",
      "personal-settings",
      "user-data"
    ],
    "navgroup-observation": [
      "observation-center",
      "status",
      "observe-tools",
      "call-records",
      "observe-autonomy",
      "observe-char-permissions",
      "observe-mood",
      "observe-listening",
      "observe-hidden",
      "observe-memory",
      "observe-memory-events",
      "observe-memory-summary",
      "memory-consolidation",
      "observe-provenance",
      "observe-vector",
      "observe-dream",
      "observe-visual",
      "observe-dream-prompt",
      "observe-dream-operations",
      "observe-group-arbiter",
      "observe-spend",
      "observe-resource-completeness",
      "scheduler"
    ]
  }
};

window.AdminCopy = (() => {
  const originals = new WeakMap();
  function textKey(node) {
    if(originals.has(node)) return originals.get(node).key;
    const el=node.parentElement, translated=el.closest('[data-i18n]');
    const scope=el.closest('[id]')?.id || el.closest('.page')?.id || 'shell';
    const text=node.textContent.trim();
    const key=translated?'i18n:'+translated.dataset.i18n:scope+':'+text;
    originals.set(node,{key,text});return key;
  }
  function apply(overrides={}) {
    const root=document.getElementById('app'); if(!root) return;
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()) {
      const node=walker.currentNode;
      if(!node.textContent.trim() || node.parentElement.closest('script,style,textarea,select,[contenteditable]')) continue;
      const key=textKey(node); let value=overrides[key] ?? AdminCopyDefaults.text[key];
      if(value===undefined && !key.startsWith('i18n:')) {
        const original=originals.get(node).text;
        const entry=Object.entries(AdminCopyDefaults.text).find(([k])=>!k.startsWith('i18n:') && k.slice(k.indexOf(':')+1)===original);
        if(entry) value=entry[1];
      }
      if(value!==undefined && node.textContent!==value) node.textContent=value;
      if(value!==undefined && !value.trim() && node.parentElement.matches('p,small.admin-source-hint')) node.parentElement.hidden=true;
    }
  }
  let pending=false;
  new MutationObserver(()=>{if(!pending){pending=true;requestAnimationFrame(()=>{
    pending=false;let edits={};try {edits=JSON.parse(localStorage.getItem('presencekit-admin-edits-v1')||'{}').text || {};} catch(_) {}
    for(const k of Object.keys(edits)) if(edits[k]==='【删掉】') edits[k]='';
    apply(edits);
  });}}).observe(document.getElementById('app'),{subtree:true,childList:true,characterData:true});
  apply();return {apply,textKey};
})();
