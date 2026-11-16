"""Turn-local read reuse with tool, principal, permission and data revisions."""
from datetime import datetime, timezone
import hashlib
import json

READ_TOOLS = frozenset({"read_life_records", "list_self_capabilities", "read_self_action_history"})


class ReadCache:
    def __init__(self, uid, char_id, *, origin):
        self.scope = (str(uid), str(char_id), origin)
        self._results = {}

    def key(self, name, arguments):
        if name not in READ_TOOLS: return None
        from core.config_loader import get_config
        from core.sandbox import get_paths
        paths = get_paths()
        uid, char_id, _ = self.scope
        try:
            versions = []
            if name == "read_life_records":
                db = paths.life_records_db(char_id=char_id)
                files = [db, db.with_name(db.name + '-wal')]
            elif name == "list_self_capabilities":
                files = [paths.self_management_state(uid, char_id=char_id)]
            else:
                files = [paths.self_management_state(uid, char_id=char_id),
                         paths.self_management_audit(uid, char_id=char_id),
                         paths.tool_audit(uid, char_id=char_id, day=datetime.now(timezone.utc).date().isoformat())]
            for path in files:
                stat = path.stat() if path.exists() else None
                versions.append((stat.st_mtime_ns, stat.st_size, stat.st_ino) if stat else None)
            from core.tool_runtime.state import _TOOL_REGISTRY
            registration = _TOOL_REGISTRY.get(name, {})
            controls = {key: value for key, value in registration.items() if key not in {"func", "invoke"}}
            # Config hashes include current feature/read grants. No config value
            # or secret is retained in the key or sent to observability.
            revision = hashlib.sha256(json.dumps([get_config(), versions, controls], sort_keys=True, default=str).encode()).hexdigest()
            return (name, self.scope, json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(',', ':')), revision)
        except (OSError, ValueError, TypeError):
            return None  # uncertain freshness/permission state is never cached

    def get(self, key):
        return self._results.get(key) if key is not None else None

    def put(self, name, arguments, value, *, successful, expected_key):
        if not successful: return
        # A concurrent revision/permission update while reading makes this
        # result unsuitable for reuse even if the tool itself succeeded.
        key = self.key(name, arguments)
        if key is not None and key == expected_key: self._results[key] = value

    def clear(self):
        self._results.clear()
