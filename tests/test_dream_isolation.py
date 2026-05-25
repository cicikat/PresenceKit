import json

import pytest

from core.memory import episodic_memory, event_log, mid_term, short_term, user_identity


@pytest.mark.asyncio
async def test_dream_artifacts_are_never_retrieved_by_reality_loaders(sandbox):
    uid = "dream_isolation_uid"
    sentinel = "DREAM_ISOLATION_SENTINEL__never_retrieve_contract"
    boundary = {
        "never_retrieve": True,
        "not_memory_source": True,
        "reality_boundary": "dream_only",
    }

    archive_dir = sandbox.dreams_archive_dir()
    summaries_dir = sandbox.dreams_summaries_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    (archive_dir / "fake_archive.json").write_text(
        json.dumps(
            {
                **boundary,
                "uid": uid,
                "content": f"archive payload {sentinel}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (summaries_dir / "fake_summary.json").write_text(
        json.dumps(
            {
                **boundary,
                "uid": uid,
                "summary": f"summary payload {sentinel}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    haystacks = [
        json.dumps(episodic_memory.retrieve(uid, topic=sentinel, top_k=5), ensure_ascii=False),
        await event_log.search(uid, sentinel),
        json.dumps(short_term.load_for_prompt(uid), ensure_ascii=False),
        mid_term.format_for_prompt(uid),
        json.dumps(await user_identity.load(uid), ensure_ascii=False),
    ]

    assert all(sentinel not in haystack for haystack in haystacks)
