"""Guards for the proposed session-scope contract. Not part of frozen v1."""

from __future__ import annotations

import json
from pathlib import Path


PROPOSED_ROOT = Path(__file__).parent / "protocol_fixtures" / "session-scope-proposed"
V1_ROOT = Path(__file__).parent / "protocol_fixtures" / "v1"
CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "session-scope-contract.md"


def _load(root: Path, name: str) -> object:
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_proposed_bundle_is_marked_unshipped_and_complete():
    manifest = _load(PROPOSED_ROOT, "manifest.json")
    assert manifest["status"] == "proposed-not-shipped"
    assert manifest["fixture_version"] == "session-scope-proposed"
    listed = {item["file"] for item in manifest["cases"]}
    actual = {path.name for path in PROPOSED_ROOT.glob("*.json")} - {"manifest.json"}
    assert listed == actual
    assert all(item.get("status") != "shipped" for item in manifest["cases"])
    for name in actual:
        payload = _load(PROPOSED_ROOT, name)
        if isinstance(payload, dict):
            assert payload.get("status") == "proposed-not-shipped"


def test_proposed_bundle_is_not_in_frozen_v1_manifest():
    v1 = _load(V1_ROOT, "manifest.json")
    v1_files = {item["file"] for item in v1["cases"]}
    proposed = {path.name for path in PROPOSED_ROOT.glob("*.json")}
    assert not (v1_files & proposed)
    assert v1["fixture_version"] == "v1"


def test_contract_doc_does_not_claim_shipped():
    text = CONTRACT.read_text(encoding="utf-8")
    assert "proposed-not-shipped" in text
    assert "落定前不得" in text
    assert "Dream settings" in text
    assert "exactly-once" in text.lower() or "不承诺 exactly-once" in text


def test_legacy_chat_forbids_char_id_on_old_servers():
    payload = _load(PROPOSED_ROOT, "legacy_chat_forbidden.json")
    assert "char_id" in payload["forbidden_on_legacy_chat"]
    for body in payload["allowed_legacy_bodies"]:
        assert "char_id" not in body["body"]
        assert "session_id" not in body["body"]


def test_whoami_missing_capabilities_means_unsupported():
    payload = _load(PROPOSED_ROOT, "whoami_discovery.json")
    current = payload["current_response"]["body"]
    assert "capabilities" not in current
    supported = payload["proposed_supported"]["body"]["capabilities"]
    assert supported["session_scope"] == "v1"


def test_mobile_shared_seq_forbids_skip_ack():
    payload = _load(PROPOSED_ROOT, "mobile_shared_seq.json")
    assert payload["queue_scope"].startswith("origin+owner")
    assert payload["forbidden_ack"]["body"]["ack_seq"] == 11
    assert payload["allowed_ack_if_filtered"]["body"]["ack_seq"] == 10
    messages = payload["poll_response"]["body"]["messages"]
    assert {item["char_id"] for item in messages} == {
        "fixture_character",
        "fixture_peer_character",
    }


def test_id_taxonomy_keeps_names_distinct():
    payload = _load(PROPOSED_ROOT, "id_taxonomy.json")
    names = [item["name"] for item in payload["ids"]]
    assert names == sorted(set(names), key=names.index)
    assert "request_id" in names
    assert "client_turn_id" in names
    assert payload["ids"][0]["name"] == "request_id"
    shipped = {item["name"] for item in payload["ids"] if item["state"] == "shipped"}
    proposed = {item["name"] for item in payload["ids"] if item["state"] == "proposed"}
    assert "msg_id" in shipped and "turn_id" in shipped and "seq" in shipped
    assert "request_id" in proposed and "session_id" in proposed
