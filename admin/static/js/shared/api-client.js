// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
async function withAdminDeadline(run, timeoutMs, parentSignal) {
  if (!timeoutMs) return run(parentSignal);
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (parentSignal?.aborted) abort();
  else parentSignal?.addEventListener('abort', abort, {once: true});
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try { return await run(controller.signal); }
  catch (error) {
    if (timedOut) throw new Error(t('common.request_timeout', '请求超时，请重试。'));
    throw error;
  } finally {
    clearTimeout(timer);
    parentSignal?.removeEventListener('abort', abort);
  }
}

function authHeaders(extra) {
  return { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json', ...extra };
}

async function api(method, path, body, options = {}) {
  const opts = { method, headers: authHeaders(), ...(options.signal ? {signal: options.signal} : {}) };
  if (body !== undefined) opts.body = JSON.stringify(body);
  // Bound reads, including body consumption. Writes keep their existing result
  // semantics: a timeout must not imply that an uncertain save was rejected.
  return withAdminDeadline(async signal => {
    const r = await fetch(BASE + path, {...opts, ...(signal ? {signal} : {})});
    if (!r.ok) {
      const text = await r.text();
      throw new Error(`HTTP ${r.status}: ${text}`);
    }
    return r.json();
  }, method === 'GET' ? (options.timeoutMs ?? 10000) : 0, options.signal);
}

