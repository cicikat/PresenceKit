let cleanupPlan = null;
let cleanupSelectedId = '';
let cleanupPreviewGeneration = 0;
let cleanupRestoreOperation = '';
const cleanupKindLabels = {
  memory:'对话与记忆（含向量索引）', characters:'角色状态、花园与作品', dreams:'梦境数据',
  self:'角色文件', self_meta:'角色文件历史', self_db:'角色资料库', character_library:'阅读资料',
  autonomy:'自主活动状态', qzone:'角色空间接入状态', self_management:'角色权限状态',
  tool_audit:'工具记录', coplay:'共玩数据', chat_artifacts:'聊天附件', wake_bridge:'外部事件接入状态',
  agent_tasks:'长期任务', agent_work_sessions:'工作会话', agent_agent_tasks:'任务内容',
  agent_browser_profiles:'任务浏览器资料', agent_workspace_versions:'工作区版本',
  reading:'阅读状态', activity:'活动记录', drinking:'饮水记录', relation_presence:'群聊在场状态', legacy_runtime:'旧运行数据',
  authored_user:'角色私有素材与人设', authored_legacy:'旧角色私有素材与人设', live2d:'专属 Live2D 模型', model3d:'专属 3D 模型',
};

function cleanupKindLabel(kind) {
  if (kind.startsWith('card_user_')) return '角色卡';
  if (kind.startsWith('card_legacy_')) return '旧目录角色卡';
  if (kind.startsWith('avatar_')) return '角色头像';
  return cleanupKindLabels[kind] || kind;
}

async function loadCharacterCleanup() {
  const status = document.getElementById('cleanup-status');
  status.textContent = '读取中…';
  try {
    const data = await api('GET', '/character-cleanup');
    const select = document.getElementById('cleanup-character'), selected = select.value;
    select.replaceChildren(new Option('请选择角色', ''));
    for (const item of data.characters || []) select.add(new Option(`${item.label} · ${item.id}${item.orphan ? '（仅遗留数据）' : ''}`, item.id));
    if ([...select.options].some(item => item.value === selected)) select.value = selected;
    const history = document.getElementById('cleanup-history'); history.replaceChildren();
    for (const item of data.history || []) {
      const row = document.createElement('p');
      row.textContent = `${item.char_id || '未知角色'} · ${item.status} · ${item.include_data ? '含相关数据' : '仅角色卡'} `;
      if (['deleted', 'rollback_failed', 'prepared'].includes(item.status)) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-ghost btn-sm'; button.textContent = '恢复';
        button.addEventListener('click', () => restoreCharacterCleanup(item.id)); row.append(button);
      }
      history.append(row);
    }
    if (!history.children.length) history.textContent = '暂无删除记录';
    status.textContent = '';
  } catch (error) { status.textContent = '读取失败：' + error.message; }
}

async function openCharacterCleanup() {
  cleanupSelectedId = document.getElementById('cleanup-character').value;
  if (!cleanupSelectedId) return;
  const dialog = document.getElementById('cleanup-dialog');
  document.getElementById('cleanup-dialog-id').textContent = cleanupSelectedId;
  document.getElementById('cleanup-data').checked = true;
  document.getElementById('cleanup-confirm-id').value = '';
  document.getElementById('cleanup-dialog-status').textContent = '';
  document.getElementById('cleanup-data').onchange = refreshCharacterCleanupPreview;
  dialog.showModal();
  await refreshCharacterCleanupPreview();
}

async function refreshCharacterCleanupPreview() {
  const generation = ++cleanupPreviewGeneration;
  cleanupPlan = null;
  const submit = document.getElementById('cleanup-submit'); submit.disabled = true;
  const includeData = document.getElementById('cleanup-data').checked;
  document.getElementById('cleanup-mode-hint').textContent = includeData
    ? '将移除角色卡及下面列出的专属数据，先保留可恢复备份。'
    : '只移除角色卡。相关数据继续保留，重建同 ID 角色后可能恢复旧记忆与状态。';
  const root = document.getElementById('cleanup-preview'); root.textContent = '预览中…';
  try {
    const plan = await api('GET', `/character-cleanup/${encodeURIComponent(cleanupSelectedId)}/preview?include_data=${includeData}`);
    if (generation !== cleanupPreviewGeneration) return;
    cleanupPlan = plan;
    root.replaceChildren();
    for (const item of plan.items || []) {
      const row = document.createElement('p'); row.textContent = `${cleanupKindLabel(item.kind)}：${item.files} 个文件 · ${(item.bytes / 1024).toFixed(1)} KB`; root.append(row);
    }
    for (const reason of plan.blockers || []) {
      const row = document.createElement('p'); row.setAttribute('role', 'alert'); row.textContent = reason; root.append(row);
    }
    if (!(plan.items || []).length) root.textContent = '没有可删除内容';
    submit.disabled = !plan.can_delete;
  } catch (error) {
    if (generation === cleanupPreviewGeneration) root.textContent = '预览失败：' + error.message;
  }
}

function closeCharacterCleanup() {
  ++cleanupPreviewGeneration; cleanupPlan = null;
  document.getElementById('cleanup-dialog').close();
}

async function submitCharacterCleanup() {
  if (!cleanupPlan?.can_delete) return;
  const submit = document.getElementById('cleanup-submit'); submit.disabled = true;
  try {
    await api('DELETE', `/character-cleanup/${encodeURIComponent(cleanupSelectedId)}`, {
      include_data: cleanupPlan.include_data, fingerprint: cleanupPlan.fingerprint,
      confirm_id: document.getElementById('cleanup-confirm-id').value.trim(),
    });
    closeCharacterCleanup(); await loadCharacterCleanup();
    document.getElementById('cleanup-status').textContent = '已删除，备份可从记录中恢复。';
  } catch (error) {
    document.getElementById('cleanup-dialog-status').textContent = '删除失败：' + error.message;
    await refreshCharacterCleanupPreview();
  }
}

async function restoreCharacterCleanup(operation) {
  cleanupRestoreOperation = operation;
  document.getElementById('cleanup-restore-status').textContent = '';
  document.getElementById('cleanup-restore-dialog').showModal();
}

function cancelCharacterRestore() { document.getElementById('cleanup-restore-dialog').close(); }

async function confirmCharacterRestore(...args) {
  const button = args[args.length - 1]; button.disabled = true;
  try {
    await api('POST', `/character-cleanup/restore/${cleanupRestoreOperation}`, {});
    cancelCharacterRestore(); await loadCharacterCleanup();
    document.getElementById('cleanup-status').textContent = '已恢复';
  } catch (error) { document.getElementById('cleanup-restore-status').textContent = '恢复失败：' + error.message; }
  finally { button.disabled = false; }
}
