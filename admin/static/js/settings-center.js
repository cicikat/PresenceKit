// Settings pages reuse the existing scoped APIs; no second configuration store.
function creationAssetRow(item, key, enabledIds) {
  const checked = (enabledIds || []).includes(item.id) ? 'checked' : '';
  const subtitle = item.label && item.label !== item.id
    ? t('settings_center.asset_id_hint','提交 id：{id}',{id:item.id})
    : t('settings_center.asset_no_title_hint','无标题，回退文件名');
  return `<label class="admin-setting-row"><span><strong>${escapeHtml(item.label||item.id)}</strong><small>${escapeHtml(subtitle)}</small></span><input type="checkbox" data-creation-asset="${key}" value="${escapeHtml(item.id)}" ${checked}></label>`;
}
async function loadCreationAssets() {
  const root=document.getElementById('creation-assets'); root.textContent=t('settings_center.loading',"读取中…");
  try {
    const data=await api('GET','/settings/prompt-assets');
    const loreRows=(data.lorebooks||[]).map(item=>creationAssetRow(item,'enabled_lorebooks',data.active.enabled_lorebooks)).join('')
      || `<p class="admin-description">${escapeHtml(t('settings_center.no_lorebooks',"暂无现实世界书"))}</p>`;
    const jailbreakRows=(data.jailbreaks||[]).map(item=>creationAssetRow(item,'enabled_jailbreaks',data.active.enabled_jailbreaks)).join('')
      || `<p class="admin-description">${escapeHtml(t('settings_center.no_jailbreaks',"暂无现实提示词"))}</p>`;
    root.innerHTML=`<div class="admin-settings-list">
      <label class="admin-setting-row"><span><strong>${escapeHtml(t('settings_center.current_character',"当前角色"))}</strong><small>${escapeHtml(t('settings_center.current_character_hint',"决定现实对话使用哪张角色卡。"))}</small></span><select id="creation-character">${data.characters.map(c=>`<option value="${escapeHtml(c.id)}" ${c.id===data.active.active_character?'selected':''}>${escapeHtml(c.label||c.id)}</option>`).join('')}</select></label>
    </div>
    <h3>${escapeHtml(t('settings_center.enabled_reality_lorebooks',"启用的现实世界书"))}</h3>
    <p class="admin-description">${escapeHtml(t('settings_center.enabled_reality_lorebooks_hint',"显示标题或关键词；保存仍提交 id。"))}</p>
    <div class="admin-settings-list">${loreRows}</div>
    <h3>${escapeHtml(t('settings_center.enabled_reality_prompts',"启用的现实提示词"))}</h3>
    <p class="admin-description">${escapeHtml(t('settings_center.enabled_reality_prompts_hint',"破限文件优先显示条目标题。"))}</p>
    <div class="admin-settings-list">${jailbreakRows}</div>
    <div class="admin-action-group"><button class="btn btn-primary" data-action="saveCreationAssets">${t('settings_center.save_enabled_assets',"保存启用组合")}</button></div>
    <h3>${escapeHtml(t('settings_center.character_avatar',"角色头像"))}</h3>
    <p class="admin-description">${escapeHtml(t('settings_center.character_avatar_png_jpeg_webp_up_to_5_mb',"角色头像（PNG / JPEG / WebP，最大 5 MB）"))}</p>
    <div class="admin-settings-list">
      <label class="admin-setting-row"><span><strong>${escapeHtml(t('settings_center.character_avatar_file',"头像文件"))}</strong><small>${escapeHtml(t('settings_center.character_avatar_file_hint',"上传覆盖当前所选角色的运行时头像。"))}</small></span><input type="file" id="creation-avatar" accept="image/png,image/jpeg,image/webp"></label>
    </div>
    <div class="admin-action-group"><button class="btn btn-ghost" data-action="uploadCreationAvatar">${t('settings_center.upload_selected_character_avatar',"上传所选角色头像")}</button><button class="btn btn-ghost" data-action="clearCreationAvatar">${t('settings_center.restore_selected_character_default_avatar',"恢复所选角色默认头像")}</button></div>
    <p id="creation-result" role="status"></p>`;
    bindPageActions(root);
  } catch(error){centerError(root,error);}
}
async function withCreationSave(save) {
  const root=document.getElementById('creation-assets'), controls=[...root.querySelectorAll('button,input,select')];
  controls.forEach(el=>el.disabled=true);
  try{await save();document.getElementById('creation-result').textContent=t('settings_center.saved',"已保存");}
  catch(error){document.getElementById('creation-result').textContent=t('settings_center.save_error','保存失败：{error}',{error:error.message});}
  finally{controls.forEach(el=>el.disabled=false);}
}
function saveCreationAssets() {
  const body={active_character:document.getElementById('creation-character').value};
  for(const key of ['enabled_lorebooks','enabled_jailbreaks'])body[key]=[...document.querySelectorAll(`[data-creation-asset="${key}"]:checked`)].map(el=>el.value);
  return withCreationSave(()=>api('PATCH','/settings/prompt-assets',body));
}
function uploadCreationAvatar() {
  const id=document.getElementById('creation-character').value, file=document.getElementById('creation-avatar').files[0];
  return withCreationSave(async()=>{
    if(!file||file.size>5*1024*1024||!['image/png','image/jpeg','image/webp'].includes(file.type))throw new Error(t('settings_center.choose_a_png_jpeg_or_webp_image_up_to_5_mb',"请选择不超过 5 MB 的 PNG、JPEG 或 WebP 图片"));
    const body=new FormData();body.append('file',file);
    const headers=authHeaders();delete headers['Content-Type'];
    const result=await fetch(BASE+`/settings/characters/${encodeURIComponent(id)}/avatar`,{method:'POST',headers,body});
    if(!result.ok)throw new Error(`HTTP ${result.status}`);
  });
}
function clearCreationAvatar(){const id=document.getElementById('creation-character').value;return withCreationSave(()=>api('DELETE',`/settings/characters/${encodeURIComponent(id)}/avatar`));}
function centerLink(page, label) {
  return `<button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='${escapeHtml(JSON.stringify([page]))}'>${escapeHtml(label)}</button>`;
}
function centerError(root, error) {
  root.textContent = t('settings_center.load_error','读取失败：{error}',{error:error.message});
}
function centerFeatureNames() { return {
  qq:t('settings_center.qq_channel',"QQ 通道"), mail:t('settings_center.mail_channel',"邮件通道"), visual_perception:t('settings_center.screen_perception',"视觉感知"), spend:t('settings_center.spending_intentions',"支出意向"), practice:t('settings_center.autonomous_practice',"自主练习"),
  action_trace:t('settings_center.action_trace',"行为记录"), self_management:t('settings_center.self_management',"自主管理"), mcp_servers:t('settings_center.external_tool_services',"外部工具服务"), fs_access:t('settings_center.read_only_file_access',"文件只读访问"),
  workspace_access:t('settings_center.workspace_files',"工作区文件"), anti_collapse:t('settings_center.output_stability',"输出稳定性"), coplay:t('settings_center.coplay',"陪玩"), toy_autogrow:t('settings_center.autonomous_toy_growth',"玩具自主生长"),
  web_autosearch:t('settings_center.autonomous_web_search',"自主联网搜索"), performance_mapping:t('settings_center.performance_annotations',"表演标注"), private_exchange:t('settings_center.private_character_exchanges',"角色私下往来"),
  event_edge_proposer:t('settings_center.memory_event_relations',"记忆事件关联"), event_shadow_recall:t('settings_center.memory_recall_comparison',"记忆召回对照实验"),
  screen_observation:t('settings_center.on_demand_screenshot',"按需截图"), ime_ingest:t('settings_center.ime_ingest',"IME 草稿接收"), ime_awareness:t('settings_center.ime_awareness',"IME 活动理解"),
}; }
const CENTER_GROUPED_FLAGS = {
  perception:['visual_perception','screen_observation'],
  output:['coplay'],
  external:['mcp_servers'],
};
const CENTER_DESTINATIONS = {
  tool:['tools','observe-tools'], loop:['conversation-settings','observe-tools'],
  scheduler:['scheduler','observe-autonomy'], autonomy:['autonomy-settings','observe-autonomy'], tts:['tts-config','call-records'],
  screen_peek:['device-policy','observe-visual'], meta:['device-policy','observe-char-permissions'],
  sticker:['output-settings','observe-prompt'], browser:['agent-runtime-browser','observe-tools'],
};
const CENTER_FLAG_DESTINATIONS = {
  qq:['feature-center','status'], mail:['mail-config','call-records'], visual_perception:['device-policy','observe-visual'],
  screen_observation:['device-policy','observe-visual'],
  mcp_servers:['mcp','observe-tools'], self_management:['autonomy-settings','observe-autonomy'], fs_access:['tools','observe-tools'],
  workspace_access:['tools','call-records'], coplay:['coplay-config','observe-tools'], action_trace:['tools','observe-tools'],
  anti_collapse:['conversation-settings','observe-prompt'], performance_mapping:['output-settings','observe-prompt'],
};
function centerDestination(source,name) {
  return (source==='flag'?CENTER_FLAG_DESTINATIONS[name]:CENTER_DESTINATIONS[source]) || ['runtime-config','observe-runtime'];
}
function centerRestartRequired(value) { return value === true || (Array.isArray(value) && value.length > 0); }
function filterCenterFeatures(value) {
  document.querySelectorAll('#feature-center-list [data-feature-row]').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(value.toLowerCase()));
}
function centerSwitch(label, checked, source, name, disabled=false, note='') {
  const [edit,observe]=centerDestination(source,name);
  return `<div class="admin-toolbar" data-feature-row><label class="checkbox-row"><input type="checkbox" ${checked?'checked':''} ${disabled?'disabled':''} data-center-source="${source}" data-center-name="${escapeHtml(name)}" onchange="saveCenterSwitch(this)"><span>${escapeHtml(label)}</span></label><span class="admin-source-hint">${escapeHtml(note)}</span>${centerLink(edit,t('settings_center.detailed_settings',"细分设置"))}${centerLink(observe,t('settings_center.view_records',"查看记录"))}</div>`;
}
function centerAutonomyControls(config) {
  const daily = Number(config.daily_evaluation_budget ?? 12);
  const interval = Number(config.min_interval_seconds ?? 900);
  return `<div class="admin-toolbar" data-feature-row data-autonomy-controls>
    <label class="checkbox-row"><input type="checkbox" ${config.enabled?'checked':''} data-center-source="autonomy" data-center-name="enabled" onchange="saveCenterSwitch(this)"><span>${escapeHtml(t('settings_center.autonomous_activity','自主活动'))}</span></label>
    <label class="field"><span>${escapeHtml(t('autonomy.daily_limit','每日评估上限'))}</span><input type="number" min="1" max="100" value="${daily}" data-autonomy-field="daily_evaluation_budget"></label>
    <label class="field"><span>${escapeHtml(t('autonomy.min_interval','最小评估间隔（秒）'))}</span><input type="number" min="0" max="86400" value="${interval}" data-autonomy-field="min_interval_seconds"></label>
    <button type="button" class="btn btn-primary btn-sm" data-action="saveCenterAutonomy">${escapeHtml(t('settings_center.save','保存'))}</button>
    ${centerLink('autonomy-settings',t('settings_center.detailed_settings','细分设置'))}
    ${centerLink('observe-autonomy',t('settings_center.view_records','查看记录'))}
  </div>`;
}
function centerFlagNote(item) {
  return item.restart_required?t('settings_center.restart_required_after_saving',"保存后需重启"):t('settings_center.global_configuration_effective_availability_below',"全局配置；实际可用性见下方");
}
function centerFlagSwitch(flags, name, fallbackLabel) {
  if (flags.status!=='fulfilled') return t('settings_center.could_not_load',"读取失败");
  const item=(flags.value.flags||{})[name];
  if (!item) return t('settings_center.could_not_load',"读取失败");
  return centerSwitch(centerFeatureNames()[name]||item.label||fallbackLabel,item.enabled,'flag',name,false,centerFlagNote(item));
}
function centerSettledSwitch(result, label, source, enabled, note) {
  if (result.status!=='fulfilled') return label+t('settings_center.could_not_load',"：读取失败");
  return centerSwitch(label,enabled,source,'enabled',false,note||t('settings_center.global_configuration',"全局配置"));
}
async function loadFeatureCenter() {
  const root=document.getElementById('feature-center-list'); root.textContent=t('settings_center.loading',"读取中…");
  const results=await Promise.allSettled([
    api('GET','/settings/feature-flags'),api('GET','/settings/tools'),api('GET','/settings/tool-loop'),
    api('GET','/admin/control-center/effective-state'),api('GET','/scheduler/config'),api('GET','/admin/autonomy/config'),
    api('GET','/tts-config'),api('GET','/settings/screen-peek'),api('GET','/system/meta-mode'),
    api('GET','/sticker-config'),api('GET','/settings/agent-runtime-browser'),
  ]);
  const section=(title,body)=>`<section class="card"><h3>${title}</h3>${body}</section>`;
  const flags=results[0], tools=results[1], loop=results[2], state=results[3];
  const grouped=new Set(Object.values(CENTER_GROUPED_FLAGS).flat());
  const remainingFlags=flags.status==='fulfilled'?Object.entries(flags.value.flags||{}).filter(([name])=>!grouped.has(name)).map(([name,item])=>centerSwitch(centerFeatureNames()[name]||item.label,item.enabled,'flag',name,false,centerFlagNote(item))).join(''):t('settings_center.could_not_load_refresh_to_retry',"读取失败，请刷新重试");
  const peek=results[7], meta=results[8], sticker=results[9], browser=results[10];
  let html=section(t('settings_center.perception_and_computer_actions',"感知与电脑操作"), [
    centerFlagSwitch(flags,'visual_perception',t('settings_center.screen_perception',"视觉感知")),
    centerFlagSwitch(flags,'screen_observation',t('settings_center.on_demand_screenshot',"按需截图")),
    centerSettledSwitch(peek,t('settings_center.screen_content',"屏幕内容查看"),'screen_peek',peek.status==='fulfilled'&&peek.value.enabled),
    centerSettledSwitch(meta,t('status.dangermode.title',"危险模式"),'meta',meta.status==='fulfilled'&&meta.value.mode==='danger',t('settings_center.danger_mode_hint',"开启后保持到手动关闭；电脑与手机操作仍受此闸约束")),
  ].join(''));
  html+=section(t('settings_center.output_and_interaction',"输出与互动"), [
    centerSettledSwitch(sticker,t('settings_center.sticker_output',"表情包输出"),'sticker',sticker.status==='fulfilled'&&sticker.value.enabled),
    centerFlagSwitch(flags,'coplay',t('settings_center.coplay',"陪玩")),
  ].join(''));
  html+=section(t('settings_center.external_capabilities',"外部能力"), [
    centerFlagSwitch(flags,'mcp_servers',t('settings_center.external_tool_services',"外部工具服务")),
    centerSettledSwitch(browser,t('settings_center.browser_tasks',"浏览器任务"),'browser',browser.status==='fulfilled'&&browser.value.enabled),
  ].join(''));
  html+=section(t('settings_center.feature_switches',"功能总开关"),remainingFlags);
  html+=section(t('settings_center.multi_step_tool_calls',"多步工具调用"),loop.status==='fulfilled'?centerSwitch(t('settings_center.global_default',"全局默认"),loop.value.enabled,'loop','enabled',false,t('settings_center.character_cards_can_override_this_ordinary_tool_calls_are_controlled_separately',"角色卡可覆盖；普通工具调用另行控制")):t('settings_center.could_not_load',"读取失败"));
  html+=section(t('settings_center.proactive_behavior_and_voice',"主动行为与语音"), [[t('settings_center.scheduler',"调度器"),4,'scheduler'],[t('settings_center.autonomous_activity',"自主活动"),5,'autonomy'],[t('settings_center.speech_synthesis',"语音合成"),6,'tts']].map(([label,i,source])=>results[i].status==='fulfilled'?(source==='autonomy'?centerAutonomyControls(results[i].value):centerSwitch(label,results[i].value.enabled,source,'enabled',false,t('settings_center.global_configuration',"全局配置"))):label+t('settings_center.could_not_load',"：读取失败")).join(''));
  html+=section(t('settings_center.built_in_tool_permissions',"内置工具执行开关"),tools.status==='fulfilled'?(tools.value.tools||[]).map(tool=>centerSwitch(t(`tools.description.${tool.name}`,tool.description||tool.name),tool.execution_enabled,'tool',tool.name,tool.frozen,tool.frozen?t('settings_center.currently_frozen',"当前冻结"):t('settings_center.global_permission_character_scope_and_execution_conditions_still_apply',"全局执行许可；仍需满足角色范围和执行条件"))).join(''):t('settings_center.could_not_load',"读取失败"));
  html+=section(t('settings_center.effective_state',"当前生效状态"),state.status==='fulfilled'?renderChainTable(state.value.features||[]):t('settings_center.status_unavailable_this_does_not_mean_the_feature_is_disabled',"状态读取失败；不代表功能关闭"));
  root.innerHTML=html; bindPageActions(root);
}
async function saveCenterAutonomy(button) {
  const row=button.closest('[data-autonomy-controls]');
  const inputs=[...row.querySelectorAll('[data-autonomy-field]')];
  if(inputs.some(input=>!input.reportValidity()))return;
  button.disabled=true;
  try {
    await api('PATCH','/admin/autonomy/config',Object.fromEntries(inputs.map(input=>[input.dataset.autonomyField,Number(input.value)])));
    toast(t('settings_center.saved','已保存'),'ok');
    await loadFeatureCenter();
  } catch(error) {
    toast(t('settings_center.save_error','保存失败：{error}',{error:error.message}),'err');
    button.disabled=false;
  }
}
async function saveCenterSwitch(input) {
  const value=input.checked; input.disabled=true;
  try {
    let result;
    if(input.dataset.centerSource==='flag') result=await api('PUT','/settings/feature-flags',{flags:{[input.dataset.centerName]:value}});
    else if(input.dataset.centerSource==='tool') result=await api('PUT','/settings/tools',{execution_enabled:{[input.dataset.centerName]:value}});
    else if(input.dataset.centerSource==='loop') result=await api('POST','/settings/tool-loop',{enabled:value});
    else if(input.dataset.centerSource==='meta') result=await api('PATCH','/system/meta-mode',{mode:value?'danger':'safe'});
    else if(input.dataset.centerSource==='screen_peek') result=await api('POST','/settings/screen-peek',{enabled:value});
    else if(input.dataset.centerSource==='sticker') result=await api('PUT','/sticker-config',{enabled:value});
    else if(input.dataset.centerSource==='browser') result=await api('PUT','/settings/agent-runtime-browser',{enabled:value});
    else {const routes={scheduler:['PUT','/scheduler/config'],autonomy:['PATCH','/admin/autonomy/config'],tts:['PUT','/tts-config']};const [method,path]=routes[input.dataset.centerSource];result=await api(method,path,{enabled:value});}
    toast(centerRestartRequired(result?.restart_required)?t('settings_center.saved_some_settings_require_a_restart',"已保存，部分设置需重启"):t('settings_center.saved',"已保存"),'ok'); await loadFeatureCenter();
  } catch(error) { input.checked=!value; input.disabled=false; toast(t('settings_center.save_error','保存失败：{error}',{error:error.message}),'err'); }
}
let imeObservationCursor = null;
let imeObservationDevice = '';
let imeObservationRequest = 0;
async function loadImeObservation(more = false) {
  const host = document.getElementById('ime-observation');
  if (!host) return;
  const device = document.getElementById('ime-device-filter').value.trim();
  const append = more === true && device === imeObservationDevice && imeObservationCursor !== null;
  const request = ++imeObservationRequest;
  const query = new URLSearchParams({device_id: device, limit: '50'});
  if (append) query.set('before', imeObservationCursor);
  document.getElementById('ime-more').hidden = true;
  if (!append) host.textContent = '正在读取接收状态…';
  try {
    const data = await api('GET', '/observability/ime-drafts?' + query);
    if (request !== imeObservationRequest) return;
    const rows = data.entries || [];
    const content = rows.map(row => `<article><p>${row.app_package === 'com.chacha.jadeime.sync_test' ? '测试上传' : '输入草稿'} · ${escapeHtml(row.device_id)} · ${escapeHtml(row.app_package)} · ${escapeHtml(row.source)} · 修订 ${escapeHtml(String(row.revision))} · ${escapeHtml(new Date(row.updated_at).toLocaleString())}</p><details><summary>查看草稿正文（敏感内容）</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${escapeHtml(row.content)}</pre><p>编辑事件（记录不等于已发送；requested 表示删除请求）</p><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(row.edit_events || [], null, 2))}</pre></details></article>`).join('');
    const awareness = data.awareness || {};
    const awarenessHtml = `<section><h3>IME 活动理解与主动关心</h3><p>${awareness.effective ? '判定已生效' : '尚未生效：' + escapeHtml(awareness.blocking_reason || '未启用')} · 不按忙碌自动避让</p><p>模型路由：${escapeHtml(awareness.route?.effective_preset || '未配置')} · ${escapeHtml(awareness.route?.source || '')}</p><p>判定后仍由角色决定是否开口，queued 只表示进入主动候选。最终发送结果见主动性观测，source 为 ime。</p>${(data.analyses || []).map(r => `<details><summary>${escapeHtml(r.status)} · ${escapeHtml(r.result?.activity || 'unknown')} · 修订 ${escapeHtml(String(r.revision))}</summary><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(r, null, 2))}</pre></details>`).join('')}</section>`;
    const summary = data.summary;
    const counts = summary ? `<p>IME 输入草稿 ${escapeHtml(String(summary.draft_count))} 条 · 测试上传 ${escapeHtml(String(summary.test_count))} 条${summary.latest_draft_updated_at ? ' · 最新草稿更新时间：' + escapeHtml(new Date(summary.latest_draft_updated_at).toLocaleString()) : ''}</p>` : '';
    if (append) host.insertAdjacentHTML('beforeend', content);
    else host.innerHTML = `<p>${data.effective ? '接收已开启' : '接收已关闭'} · 保留 ${escapeHtml(String(data.retention_hours))} 小时</p>` + awarenessHtml + counts + (content || '<p>三小时内暂无接收记录。请检查输入法草稿记录和自动回传两个开关；保存配置会关闭自动回传，需要重新开启。测试成功不代表自动上传已开启；空列表也可能是草稿已过期。</p>');
    imeObservationCursor = data.next_before;
    imeObservationDevice = device;
    document.getElementById('ime-more').hidden = data.next_before == null;
  } catch (error) {
    if (request !== imeObservationRequest) return;
    if (!append) host.textContent = '接收状态读取失败：' + (error.message || String(error));
    else { const message = document.createElement('p'); message.textContent = '更早记录读取失败，请刷新重试。'; host.append(message); }
  }
}

async function loadServiceCenter() {
  loadLifeRecords();
  const root=document.getElementById('service-center-list'); root.textContent=t('settings_center.loading',"读取中…");
  const specs=[
    [t('settings_center.text_model',"文本模型"),'/settings/prompt-assets','model-routing',d=>{const current=d.characters?.find(c=>c.id===d.active?.active_character);return [current?.chat_configured,current?.resolved_chat_model];}],
    [t('settings_center.embedding_search',"向量检索"),'/settings/setup-status','embedding-config',d=>[d.embedding?.configured,d.embedding?.model]],
    [t('settings_center.owner_profile',"所有者资料"),'/settings/setup-status','setup',d=>[d.owner?.configured,'']],
    [t('settings_center.image_recognition',"图片识别"),'/vision-params','model-routing',d=>[typeof d.model==='string'&&typeof d.base_url==='string'?Boolean(d.model&&d.base_url):undefined,d.model]],
    [t('settings_center.text_recognition_ocr',"文字识别（OCR）"),'/image-recognition','model-routing',d=>[d.ocr?.configured??d.configured, '生活记录账单固定使用 OCR；饮食与购物车使用视觉']],
    [t('settings_center.speech_synthesis',"语音合成"),'/tts-config','tts-config',d=>[d.provider_status?.ready,d.provider]],
    [t('settings_center.external_tool_services',"外部工具服务"),'/settings/mcp','mcp',d=>[Array.isArray(d.servers)?d.servers.length>0:undefined,Array.isArray(d.servers)?t('settings_center.server_count','{count} 个服务；连接状态见详情',{count:d.servers.length}):t('settings_center.service_status_not_returned',"服务状态未返回")]],
    [t('settings_center.mail',"邮件"),'/settings/mail','mail-config',d=>[d.configured,'']],
    [t('settings_center.diary_source',"日记数据源"),'/settings/diary','diary-config',d=>[d.configured,'']],
    [t('settings_center.proxy',"代理"),'/proxy','network-config',d=>[typeof d.enabled==='boolean'?(!d.enabled||Boolean(d.http||d.https)):undefined,d.enabled?t('settings_center.enabled',"已启用"):t('settings_center.disabled_direct_connection_available',"未启用（可直接连接）")]],
  ];
  const results=await Promise.allSettled(specs.map(s=>api('GET',s[1])));
  root.innerHTML=specs.map(([label,path,page,view],i)=>{const result=results[i]; const [ready,detail]=result.status==='fulfilled'?view(result.value):[undefined,t('settings_center.could_not_load',"读取失败")]; return `<section class="card"><h3>${label}</h3><p>${result.status==='rejected'?t('settings_center.could_not_load',"读取失败"):ready===true?t('settings_center.configured_connection_not_tested',"已配置 · 未执行连通性测试"):ready===false?t('settings_center.not_configured_or_incomplete',"未配置或配置不完整"):t('settings_center.unknown_configuration_status_not_returned',"未知：接口未返回配置状态")}${detail?' · '+escapeHtml(detail):''}</p>${centerLink(page,t('settings_center.view_configuration',"查看配置"))}</section>`;}).join('')+`<section class="card">${centerLink('role-bindings',t('settings_center.character_model_and_asset_bindings',"角色模型与资源绑定"))}${centerLink('auth-tokens',t('settings_center.message_channels_and_credentials',"消息通道与访问凭据"))}</section>`;
  bindPageActions(root);
}

async function loadLifeRecords() {
  const host = document.getElementById('life-status');
  if (!host) return;
  host.textContent = '正在读取生活记录状态…';
  try {
    const data = await api('GET', '/settings/life-records');
    for (const [id, key] of [['enabled','enabled'],['readable','character_readable'],['background','background_sync'],['retain','retain_images']]) document.getElementById('life-' + id).checked = !!data[key];
    const labels = {diet:'饮食',bill:'账单',cart:'购物车'};
    const states = {pending:'等待识别',processing:'识别中',ready:'已保存描述',failed:'识别失败'};
    const errors = {ValidationError:'旧版字段校验失败，可重试',JSONDecodeError:'旧版 JSON 解析失败，可重试',EmptyRecognition:'没有识别到可读内容，请检查原图',TimeoutError:'识别超时，可重试',ValueError:'识别服务请求失败，请检查连接'};
    host.innerHTML = `<p>${data.enabled ? '同步已开启' : '同步已关闭'} · 手填内容与图片描述分开保存</p>` +
      `<p>${data.character_readable ? '角色可读取生活记录' : '角色读取已关闭'}。上传识别完成后，未读资料会随下一次对话或主动机会提供；已读不代表已经回复。</p>` +
      `<p class="admin-description">${data.continuity && !data.continuity.unavailable ? `当前角色待评估资料 ${Number(data.continuity.pending_count || 0)} 条（单轮最多 3 条）；近期主动工具结果 ${Number(data.continuity.tool_results?.length || 0)} 条，保留 24 小时。` : '资料接续状态暂不可用。'}</p>` +
      `<div class="admin-settings-list">${Object.entries(data.recognition_routes || {}).map(([category,route])=>`<div class="admin-setting-row"><span><strong>${escapeHtml(labels[category] || category)}</strong><small>${route.route === 'ocr' ? '独立 OCR · 提取账单文字' : '通用视觉 · 自然语言描述'}</small></span><span class="admin-status-badge">${route.effective ? '已配置，待实际识别验证' : route.configured ? '同步关闭' : '未配置，任务等待'}</span></div>`).join('')}</div>` +
      `<div class="admin-toolbar">${Object.entries(states).map(([key,label])=>`<span class="admin-status-badge">${label} ${Number(data.tasks?.[key] || 0)}</span>`).join('')}</div>` +
      ((data.failures || []).length ? `<div class="admin-settings-list">${data.failures.map(row=>`<div class="admin-setting-row"><span><strong>${escapeHtml(errors[row.error] || '识别失败，请检查服务连接后重试')}</strong><small>记录 ${escapeHtml(row.id)}</small></span><button class="btn btn-ghost btn-sm" data-action="retryLifeRecord" data-action-args='${escapeHtml(JSON.stringify([row.id]))}'>重试识别</button></div>`).join('')}</div>` : '<p class="admin-description">没有失败任务。</p>') +
      `<details><summary>设备回执与技术详情</summary><pre>${escapeHtml(JSON.stringify({设备回执:data.devices,操作审计:data.audit,失败:data.failures,资料接续:data.continuity},null,2))}</pre></details>`;
    bindPageActions(host);
  } catch (error) { host.textContent = '生活记录状态读取失败：' + error.message; }
}
async function saveLifeRecords() {
  try {
    await api('PUT', '/settings/life-records', {enabled:document.getElementById('life-enabled').checked,character_readable:document.getElementById('life-readable').checked,background_sync:document.getElementById('life-background').checked,retain_images:document.getElementById('life-retain').checked});
    await loadLifeRecords();
  } catch (error) { document.getElementById('life-status').textContent = '保存失败：' + error.message; }
}
async function retryLifeRecord(recordId) {
  try {
    const id = String(recordId || '').trim();
    if (!id) return;
    await api('POST', '/settings/life-records/' + encodeURIComponent(id) + '/retry');
    await loadLifeRecords();
  } catch (error) { document.getElementById('life-status').textContent = '重试失败：' + error.message; }
}
let roleBindingGeneration=0;
let roleBindingCharacters=[];
async function loadRoleBindings() {
  const select=document.getElementById('binding-character');
  try {const data=await api('GET','/characters'); roleBindingCharacters=data.characters||[]; select.innerHTML=roleBindingCharacters.filter(c=>!c.hidden).map(c=>`<option value="${escapeHtml(c.id)}" ${c.id===data.active_id?'selected':''}>${escapeHtml(c.label||c.id)}</option>`).join(''); await loadRoleBindingDetail();}
  catch(error){centerError(document.getElementById('role-binding-fields'),error);}
}
async function loadRoleBindingDetail() {
  const id=document.getElementById('binding-character').value, generation=++roleBindingGeneration;
  const root=document.getElementById('role-binding-fields');root.textContent=t('settings_center.loading',"读取中…"); if(!id){root.textContent=t('settings_center.select_a_character',"请选择角色");return;}
  try {
    const [routing,profiles,assets]=await Promise.all([api('GET',`/character/${encodeURIComponent(id)}/model-routing`),api('GET','/model-presets/routing-profiles'),api('GET',`/character/${encodeURIComponent(id)}/asset-bindings`)]);
    if(generation!==roleBindingGeneration)return;
    const writable=/\.json$/i.test(roleBindingCharacters.find(c=>c.id===id)?.filename||'');
    root.innerHTML=`<section class="card"><h3>${t('settings_center.character_model_binding',"角色模型绑定")}</h3><p>${escapeHtml(t('settings_center.routing_summary','当前方案：{profile} · 模型连接：{preset} · 当前模型：{model}',{profile:routing.effective_profile||'—',preset:routing.resolved_chat_preset||'—',model:routing.resolved_chat_model||t('settings_center.not_configured',"未配置")}))}</p><p>${routing.model_routing?t('settings_center.character_binding',"使用角色绑定"):t('settings_center.follow_global',"跟随全局")} · ${routing.chat_configured?t('settings_center.configured_connection_not_tested',"配置齐全（未测试连接）"):t('settings_center.incomplete_configuration',"配置不完整")}</p><label class="field">${t('settings_center.model_profile',"模型方案")}<select id="binding-profile"><option value="">${t('settings_center.follow_global',"跟随全局")}</option>${(profiles.profiles||[]).map(p=>`<option value="${escapeHtml(p.name)}" ${p.name===routing.model_routing?'selected':''}>${escapeHtml(p.name)}</option>`).join('')}</select></label><div class="admin-action-group"><button class="btn btn-primary" data-action="saveRoleBinding">${t('settings_center.save_binding',"保存绑定")}</button><button class="btn btn-ghost" data-action="resetRoleBinding">${t('settings_center.reset_to_global',"重置为跟随全局")}</button></div><p id="binding-save-status" role="status"></p></section><section class="card"><h3>${t('settings_center.voice_and_appearance_bindings',"声音与形象绑定")}</h3>${[['tts_preset',t('settings_center.voice_preset',"语音预设")],['sticker_pack',t('settings_center.sticker_pack',"表情包池")],['live2d_model',t('settings_center.live2d_model',"Live2D 形象")],['model_3d',t('settings_center.3d_model',"3D 形象")]].map(([key,label])=>`<label class="field">${label}<input type="text" data-binding-asset="${key}" value="${escapeHtml(assets[key]||'')}" placeholder="${t('settings_center.leave_empty_to_use_defaults','留空跟随默认配置')}"></label>`).join('')}<button class="btn btn-primary" data-action="saveRoleAssets">${t('settings_center.save_asset_bindings',"保存资源绑定")}</button></section>`;
    if(!writable){root.querySelectorAll('input,select,button').forEach(el=>el.disabled=true);root.insertAdjacentHTML('afterbegin',`<p role=\"status\">${t('settings_center.plain_text_character_cards_do_not_support_persistent_bindings_use_a_json_card_in_creation_first',"纯文本角色卡不支持持久绑定，请先在创作中使用 JSON 角色卡。")}</p>`);}
    bindPageActions(root);
  }catch(error){if(generation===roleBindingGeneration)centerError(root,error);}
}
async function persistRoleBinding(reset) {
  const select=document.getElementById('binding-character'),id=select.value; select.disabled=true;
  const root=document.getElementById('role-binding-fields'); const buttons=root.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
  try {await api('PATCH',`/character/${encodeURIComponent(id)}/model-routing`,{model_routing:reset?null:document.getElementById('binding-profile').value||null}); if(document.getElementById('binding-character').value===id)await loadRoleBindingDetail();toast(reset?t('settings_center.reset_future_calls_follow_the_global_profile',"已重置，后续跟随全局模型方案"):t('settings_center.character_model_binding_saved',"已保存角色模型绑定"),'ok');}
  catch(error){toast(t('settings_center.save_error','保存失败：{error}',{error:error.message}),'err');buttons.forEach(b=>b.disabled=false);}
  finally{select.disabled=false;}
}
function saveRoleBinding(){return persistRoleBinding(false);}
function resetRoleBinding(){return persistRoleBinding(true);}
let centerRecordsGeneration=0;
async function openCenterRecords(source) {
  await goto('call-records');
  document.getElementById('record-source').value=source;
  return loadUnifiedRecords();
}
function centerRecordRows(data) {
  return data.entries || data.tasks?.entries || (Array.isArray(data.tasks)?data.tasks:undefined) || data.sessions || data.records || [];
}
function centerRecordStatus(row) {
  const status=typeof row.ok==='boolean'?(row.ok?'succeeded':'failed'):row.status||row.state||'unknown';
  return t('settings_center.record_status_'+status,status);
}
function centerRecordTime(row) {
  const value=row.ts??row.updated_at??row.created_at??row.started_at;
  if(!value)return '—';
  const date=new Date(typeof value==='number'?value*1000:value);
  return Number.isNaN(date.valueOf())?'—':date.toLocaleString();
}
async function loadUnifiedRecords(){
  const generation=++centerRecordsGeneration;
  const root=document.getElementById('unified-records'),path=document.getElementById('record-source').value;
  root.textContent=t('settings_center.loading',"读取中…");
  try{
    const data=await api('GET',path);if(generation!==centerRecordsGeneration)return;
    const query=(document.getElementById('record-filter')?.value||'').trim().toLowerCase();
    const rows=centerRecordRows(data).filter(row=>!query||JSON.stringify(row).toLowerCase().includes(query));
    const capability=data.capability||(typeof data.enabled==='boolean'?data:null);
    const summary=capability?`<p>${escapeHtml(centerRecordStatus(capability))} · ${t('settings_center.workspace_roots','授权目录数')}：${escapeHtml(String(capability.root_count??'—'))}</p>`:'';
    root.innerHTML=summary+`<p>${t('settings_center.records_count','显示 {count} 条记录',{count:rows.length})}${data.truncated||data.tasks?.truncated?' · '+t('settings_center.records_truncated','仅显示最近记录'):''}</p>`+
      (rows.length?rows.map(row=>`<section class="card"><h3>${escapeHtml(centerRecordStatus(row))} · ${escapeHtml(row.caller||row.capability||row.kind||'—')}</h3><p>${escapeHtml(centerRecordTime(row))} · ${escapeHtml(row.provider||row.scope?.char_id||'—')} · ${escapeHtml(row.model||row.purpose||row.source||'—')}</p><p>${escapeHtml(row.error_category||row.error_code||row.blocking_reason||'')}${row.duration_ms!==undefined?' · '+escapeHtml(String(row.duration_ms))+' ms':''}</p><details><summary>${t('settings_center.record_details',"详细记录")}</summary><pre>${escapeHtml(JSON.stringify(row,null,2))}</pre></details></section>`).join(''):t('settings_center.no_records',"暂无记录"))+
      `<details><summary>${t('settings_center.ledger_status',"台账状态")}</summary><pre>${escapeHtml(JSON.stringify(data,null,2))}</pre></details>`;
  }catch(error){if(generation===centerRecordsGeneration)centerError(root,error);}
}
async function saveRoleAssets(){
  const select=document.getElementById('binding-character'), id=select.value, generation=roleBindingGeneration;
  const root=document.getElementById('role-binding-fields'), body={};
  root.querySelectorAll('[data-binding-asset]').forEach(input=>body[input.dataset.bindingAsset]=input.value.trim()||null);
  const controls=[...root.querySelectorAll('button,input,select')]; controls.forEach(el=>el.disabled=true); select.disabled=true;
  try{await api('PATCH',`/character/${encodeURIComponent(id)}/asset-bindings`,body);if(generation===roleBindingGeneration&&select.value===id){toast(t('settings_center.asset_bindings_saved',"资源绑定已保存"),'ok');await loadRoleBindingDetail();}}
  catch(error){toast(t('settings_center.save_error','保存失败：{error}',{error:error.message}),'err');}
  finally{select.disabled=false;if(generation===roleBindingGeneration)controls.forEach(el=>el.disabled=false);}
}
function conversationFields() { return [
  [t('settings_center.chat_mode',"聊天模式"),'/chat-mode','PUT',[['mode',t('settings_center.mode',"模式"),['chat','roleplay'],[t('settings_center.everyday_companionship',"日常陪伴"),t('settings_center.roleplay',"角色扮演")]]]],
  [t('settings_center.conversation_style',"对话风格"),'/chat-style','PUT',[['style',t('settings_center.style',"风格"),['chat','roleplay'],[t('settings_center.dialogue_focused',"对白为主"),t('settings_center.first_person_immersion',"第一人称沉浸")]]]],
  [t('settings_center.split_replies',"分条发送"),'/chat-multi-message','PUT',[['enabled',t('settings_center.multiple_message_bubbles',"多消息气泡"),'boolean']]],
  [t('settings_center.multi_step_tool_calls',"多步工具调用"),'/settings/tool-loop','POST',[['enabled',t('settings_center.enabled_by_default',"全局默认开启"),'boolean'],['max_steps',t('settings_center.maximum_steps',"最大步骤"),'number',1,8],['total_timeout_s',t('settings_center.total_timeout_seconds',"总超时（秒）"),'number',5,720],['nudge_hint',t('settings_center.continuation_hint',"继续执行提示"),'text']]],
]; }
function thinkingFieldSpecs() { return [
  ['enabled',t('settings_center.generate_reasoning',"生成思考"),'boolean'],
  ['character_voice',t('settings_center.character_voice',"角色心声文风（通用提示引导）"),'boolean'],
  ['mode',t('settings_center.method',"方式"),['auto','native','monologue'],[t('settings_center.automatic',"自动"),t('settings_center.native_reasoning',"原生思考"),t('settings_center.prefixed_monologue',"前置独白")]],
  ['apply_to_proactive',t('settings_center.apply_to_proactive_messages',"应用于主动消息"),'boolean'],
  ['monologue_max_tokens',t('settings_center.monologue_token_budget',"独白预算"),'number',32,2000],
]; }
function thinkingFieldMarkup(data) {
  const specs = thinkingFieldSpecs();
  const switches = specs.filter(([, , type]) => type === 'boolean').map(([key, label]) =>
    `<div class="admin-toolbar"><label class="checkbox-row"><input data-field="${key}" type="checkbox" ${data[key] ? 'checked' : ''}><span>${label}</span></label></div>`
  ).join('');
  const options = specs.filter(([, , type]) => type !== 'boolean').map(([key, label, type, a, b]) =>
    `<label class="field">${label}${Array.isArray(type) ? `<select data-field="${key}">${type.map((value, j) => `<option value="${value}" ${data[key] === value ? 'selected' : ''}>${a[j]}</option>`).join('')}</select>` : `<input data-field="${key}" type="${type}" value="${escapeHtml(String(data[key] ?? ''))}" ${type === 'number' ? `min="${a}" max="${b}"` : ''}>`}</label>`
  ).join('');
  return switches + options;
}
async function loadConversationSettings(){
  const root=document.getElementById('conversation-settings-fields');root.textContent=t('settings_center.loading',"读取中…");
  const results=await Promise.allSettled(conversationFields().map(s=>api('GET',s[1])));
  const thinkingJump=`<section class="card" id="conversation-thinking-jump"><h3>${t('settings_center.reasoning',"思考")}</h3><p>${t('routing.thinking_moved',"思考总开关、方式和心声已移到「模型连接与分工」。桌面客户端只负责展开显示。")}</p><button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='["model-routing"]'>${t('nav.page.model-routing',"模型连接与分工")}</button></section>`;
  root.innerHTML=conversationFields().map(([title,path,method,fields],i)=>{const result=results[i];if(result.status==='rejected')return `<section class="card"><h3>${title}</h3><p>${t('settings_center.could_not_load_refresh_to_retry',"读取失败，请刷新重试")}</p></section>`; const data=result.value;if(path==='/chat-multi-message')data.enabled=data.multi_message;return `<section class="card" id="conversation-form-${i}"><h3>${title}</h3>${fields.map(([key,label,type,a,b])=>`<label class="field">${label}${Array.isArray(type)?`<select data-field="${key}">${type.map((value,j)=>`<option value="${value}" ${data[key]===value?'selected':''}>${a[j]}</option>`).join('')}</select>`:`<input data-field="${key}" type="${type==='boolean'?'checkbox':type}" ${type==='boolean'?(data[key]?'checked':''):`value="${escapeHtml(String(data[key]??''))}"`} ${type==='number'?`min="${a}" max="${b}"`:''}>`}</label>`).join('')}<button class="btn btn-primary" data-action="saveConversationSection" data-action-args='[${i}]'>${t('settings_center.save',"保存")}</button><p role="status" data-save-status></p></section>`;}).join('')+thinkingJump;bindPageActions(root);loadOutputSegmentEnforce();loadContextConfig();loadLlmParams();
}
function showThinkingVoicePreview(data, root) {
  const host = root || document.getElementById('mr-thinking-card');
  if (!host || !data?.voice_preview) return;
  host.querySelector('[data-voice-preview]')?.remove();
  const voice = data.voice_preview;
  const box = document.createElement('div');
  box.dataset.voicePreview = 'true';
  const status = document.createElement('p');
  const autoHint = data.chat_preset_reasoning_native
    ? t('routing.thinking_auto_native',"当前 chat 连接声明了原生思考，自动模式会走 native。")
    : t('routing.thinking_auto_monologue',"当前 chat 连接未声明原生思考，自动模式会走前置独白。");
  const stateLabel = voice.effective ? t('settings_center.enabled',"已启用") : t('settings_center.currently_unavailable',"未启用");
  const reason = voice.blocking_reason || t('routing.thinking_with_main',"随主生成发送");
  status.textContent = `${t('routing.thinking_voice_status','心声引导：{state} · {reason}。风格约 24 小时轮换；情绪沿用现有平滑状态。原生摘要是否遵从由模型决定，通用提示可能影响回复措辞。',{state: stateLabel, reason})} ${autoHint}`;
  const details = document.createElement('details');
  const heading = document.createElement('summary');
  heading.textContent = t('routing.thinking_preview',"查看当前拼接提示");
  const prompt = document.createElement('pre');
  prompt.style.whiteSpace = 'pre-wrap';
  prompt.textContent = voice.prompt || '';
  details.append(heading, prompt);
  box.append(status, details);
  host.append(box);
}
async function loadThinkingSettings(){
  const fields=document.getElementById('mr-thinking-fields');
  if(!fields)return;
  fields.textContent=t('settings_center.loading',"读取中…");
  try{
    const data=await api('GET','/settings/thinking');
    fields.innerHTML=thinkingFieldMarkup(data);
    showThinkingVoicePreview(data, document.getElementById('mr-thinking-card'));
    const status=document.getElementById('mr-thinking-status');
    if(status)status.textContent='';
  }catch(error){
    fields.innerHTML=`<p>${t('settings_center.could_not_load_refresh_to_retry',"读取失败，请刷新重试")}</p>`;
  }
}
async function saveThinkingSettings(){
  const root=document.getElementById('mr-thinking-card');
  if(!root)return;
  const body={};
  const inputs=[...root.querySelectorAll('[data-field]')];
  if(inputs.some(input=>!input.reportValidity()))return;
  inputs.forEach(input=>body[input.dataset.field]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value);
  const button=root.querySelector('[data-action="saveThinkingSettings"]');
  const status=document.getElementById('mr-thinking-status');
  if(button)button.disabled=true;
  try{
    await api('POST','/settings/thinking',body);
    showThinkingVoicePreview(await api('GET','/settings/thinking'), root);
    if(status)status.textContent=t('settings_center.saved_applies_to_future_requests',"已保存，后续请求生效");
  }catch(error){
    if(status)status.textContent=t('settings_center.save_error','保存失败：{error}',{error:error.message});
  }finally{
    if(button)button.disabled=false;
  }
}
async function saveConversationSection(i){
  const [,path,method]=conversationFields()[i],root=document.getElementById(`conversation-form-${i}`),body={};
  const inputs=[...root.querySelectorAll('[data-field]')];if(inputs.some(input=>!input.reportValidity()))return;
  inputs.forEach(input=>body[input.dataset.field]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value);
  const button=root.querySelector('button');button.disabled=true;
  try{await api(method,path,body);root.querySelector('[data-save-status]').textContent=t('settings_center.saved_applies_to_future_requests',"已保存，后续请求生效");}catch(error){root.querySelector('[data-save-status]').textContent=t('settings_center.save_error','保存失败：{error}',{error:error.message});}finally{button.disabled=false;}
}
