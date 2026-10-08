const MC_BOOLEAN_FIELDS = ['enabled', 'allow_pickup', 'allow_defend', 'model_enabled'];
const MC_NUMBER_FIELDS = ['port', 'model_calls_per_session', 'model_cooldown_seconds'];
const MC_TEXT_FIELDS = ['bridge_url', 'host', 'version', 'username', 'auth', 'owner_uuid'];

async function loadMinecraftPage() {
  try {
    const data = await api('GET', '/settings/minecraft');
    for (const key of MC_BOOLEAN_FIELDS) document.getElementById('mc-' + key).checked = Boolean(data.configured[key]);
    for (const key of [...MC_NUMBER_FIELDS, ...MC_TEXT_FIELDS]) document.getElementById('mc-' + key).value = data.configured[key];
    document.getElementById('minecraft-status').textContent = `配置状态：${data.effective_state}；身体：${data.runtime.connection_state}；共玩：${data.runtime.active ? '进行中' : '未开始'}`;
    document.getElementById('minecraft-observation').textContent = JSON.stringify(data.runtime, null, 2);
  } catch (error) { toast(`Minecraft 状态读取失败：${error.message || error}`, 'err'); }
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
    const receipt = await api('POST', '/activity/minecraft/command', {action, params: {}, command_id: crypto.randomUUID()});
    document.getElementById('minecraft-result').textContent = `动作 ${action}：${receipt.status}`;
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
