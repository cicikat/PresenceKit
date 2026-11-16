// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
// Project only changed subtrees, once per frame. Disconnect during projection so
// our own text/style changes cannot schedule a second full pass.
function observeAdminChanges(project, options = {}) {
  const app = document.getElementById('app');
  const roots = new Set();
  let scheduled = false;
  let frame = null;
  let stopped = false;
  const settings = {childList: true, subtree: true, characterData: true, ...options};
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'childList') {
        record.addedNodes.forEach(node => roots.add(node));
        if (record.removedNodes.length) roots.add(record.target);
      }
      else roots.add(record.target);
    }
    if (scheduled || !roots.size) return;
    scheduled = true;
    frame = requestAnimationFrame(() => {
      frame = null;
      scheduled = false;
      if (stopped) return;
      const changed = [...roots].filter(node => node.isConnected);
      roots.clear();
      const changedSet = new Set(changed);
      const unique = changed.filter(node => {
        for (let parent = node.parentNode; parent; parent = parent.parentNode) {
          if (changedSet.has(parent)) return false;
        }
        return true;
      });
      observer.disconnect();
      try { project(unique); }
      finally { if (!stopped) observer.observe(app, settings); }
    });
  });
  observer.observe(app, settings);
  return {disconnect() {
    stopped = true;
    if (frame !== null) cancelAnimationFrame(frame);
    roots.clear();
    observer.disconnect();
  }};
}

