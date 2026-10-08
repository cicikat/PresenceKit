const MC_BOOLEAN_FIELDS = ['enabled', 'allow_pickup', 'allow_defend', 'allow_mining', 'allow_building', 'model_enabled', 'reaction_enabled'];
const MC_NUMBER_FIELDS = ['port', 'model_calls_per_session', 'model_cooldown_seconds', 'reaction_calls_per_session', 'reaction_cooldown_seconds'];
const MC_TEXT_FIELDS = ['bridge_url', 'host', 'version', 'username', 'auth', 'owner_uuid'];
let mcRoutingProfile = '';

async function loadMinecraftPage() {
  try {
    const data = await api('GET', '/settings/minecraft');
    for (const key of MC_BOOLEAN_FIELDS) document.getElementById('mc-' + key).checked = Boolean(data.configured[key]);
    for (const key of [...MC_NUMBER_FIELDS, ...MC_TEXT_FIELDS]) document.getElementById('mc-' + key).value = data.configured[key];
    document.getElementById('minecraft-status').textContent = `配置状态：${data.effective_state}；身体：${data.runtime.connection_state}；共玩：${data.runtime.active ? '进行中' : '未开始'}`;
    document.getElementById('minecraft-observation').textContent = JSON.stringify(data.runtime, null, 2);
    const routing = data.routing;
    mcRoutingProfile = routing.profile || '';
    const select = document.getElementById('mc-reaction-preset');
    select.replaceChildren();
    for (const name of ['', ...(routing.presets || [])]) {
      const option = document.createElement('option');
      option.value = name; option.textContent = name || '沿用 sensor_judge → intent → chat';
      select.append(option);
    }
    select.value = routing.configured_preset || '';
    document.getElementById('mc-reaction-route').textContent = `方案：${mcRoutingProfile || '未配置'}${routing.character_override ? '（角色覆盖全局）' : ''}；生效：${routing.effective?.effective_preset || '未配置'}；来源：${routing.effective?.source || '无'}`;
  } catch (error) { toast(`Minecraft 状态读取失败：${error.message || error}`, 'err'); }
}

async function saveMinecraftReactionRoute() {
  try {
    await api('PUT', '/settings/minecraft/routing', {profile: mcRoutingProfile, preset: document.getElementById('mc-reaction-preset').value});
    toast('快速判断路由已保存；与模型路由页共用配置', 'ok'); await loadMinecraftPage();
  } catch (error) { toast(`路由保存失败：${error.message || error}`, 'err'); }
}

async function saveMinecraftSettings() {
  const body = {};
  for (const key of MC_BOOLEAN_FIELDS) body[key] = document.getElementById('mc-' + key).checked;
  for (const key of MC_NUMBER_FIELDS) body[key] = Number(document.getElementById('mc-' + key).value);
  for (const key of MC_TEXT_FIELDS) body[key] = document.getElementById('mc-' + key).value.trim();
  try { await api('PUT', '/settings/minecraft', body); toast('Minecraft 配置已保存', 'ok'); await loadMinecraftPage(); }
  catch (error) { toast(`保存失败：${error.message || error}`, 'err'); }
}

async function minecraftSession(operation) {
  try { await api('POST', `/activity/minecraft/${operation}`); await loadMinecraftPage(); }
  catch (error) { toast(`会话操作失败：${error.message || error}`, 'err'); }
}

async function minecraftCommand(action) {
  try {
    const params = action === 'collect_iron' ? {count: Number(document.getElementById('mc-collect-count').value), radius: Number(document.getElementById('mc-collect-radius').value)} : {};
    if (action === 'build_house') {
      params.material = document.getElementById('mc-build-material').value;
      for (const axis of ['x', 'y', 'z']) {
        const raw = document.getElementById('mc-build-' + axis).value.trim();
        if (!raw || !Number.isInteger(Number(raw))) throw new Error('请填写三个整数施工坐标');
        params[axis] = Number(raw);
      }
    }
    const receipt = await api('POST', '/activity/minecraft/command', {action, params, command_id: crypto.randomUUID()});
    document.getElementById('minecraft-result').textContent = `动作 ${action}：${receipt.status}${receipt.error ? '；原因：' + receipt.error : ''}${receipt.total ? `；已确认 ${receipt.placed}/${receipt.total} 块` : ''}`;
    await loadMinecraftPage();
  } catch (error) { toast(`动作提交失败：${error.message || error}`, 'err'); }
}

async function minecraftChat() {
  const text = document.getElementById('mc-chat').value.trim();
  if (!text) return;
  try {
    const result = await api('POST', '/activity/minecraft/chat', {text});
    document.getElementById('minecraft-result').textContent = result.reply;
    document.getElementById('mc-chat').value = '';
    await loadMinecraftPage();
  } catch (error) { toast(`聊天失败：${error.message || error}`, 'err'); }
}
