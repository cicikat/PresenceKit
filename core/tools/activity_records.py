"""Reality records are scoped by dispatcher identity, never by model arguments."""
import json
from core.activity import records


async def query_activity_records(user_id, source_ref="", offset=0, limit=20,
                                 text_offset=0, keyword="", since="", until="",
                                 expected_revision="", *, char_id):
    try:
        result = (records.read(str(user_id), char_id, source_ref, offset=offset, limit=limit,
                               text_offset=text_offset, keyword=keyword, since=since,
                               until=until, expected_revision=expected_revision)
                  if source_ref else
                  records.search(str(user_id), char_id, keyword=keyword, since=since, until=until,
                                 offset=offset, limit=limit) if keyword or since or until else
                  records.list_sessions(str(user_id), char_id, offset=offset, limit=limit))
        return json.dumps(result, ensure_ascii=False)
    except (LookupError, ValueError):
        return json.dumps({"error": "source_unavailable_or_revision_changed"})
