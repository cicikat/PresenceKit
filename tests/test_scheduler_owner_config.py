"""tests/test_scheduler_owner_config.py — Brief 95 §1：owner_id/owner_birthday 校验与状态视图"""
import asyncio

import pytest
from fastapi import HTTPException

from admin.routers import scheduler


def test_put_owner_id_rejects_illegal_chars(monkeypatch):
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scheduler.put_sched_config({"owner_id": "张三@qq"}, auth=None))
    assert exc.value.status_code == 422


def test_put_owner_id_accepts_qq_number_and_strips_whitespace(monkeypatch):
    saved = {}
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: saved.update(cfg))
    result = asyncio.run(scheduler.put_sched_config({"owner_id": " 123456789 "}, auth=None))
    assert saved["owner_id"] == "123456789"
    assert result["config"]["owner_id"] == "123456789"


def test_put_owner_id_allows_clearing_to_empty(monkeypatch):
    saved = {}
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {"owner_id": "123"})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: saved.update(cfg))
    asyncio.run(scheduler.put_sched_config({"owner_id": ""}, auth=None))
    assert saved["owner_id"] == ""


def test_put_owner_birthday_rejects_bad_format(monkeypatch):
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scheduler.put_sched_config({"owner_birthday": "MM-DD"}, auth=None))
    assert exc.value.status_code == 422


def test_put_owner_birthday_rejects_impossible_date(monkeypatch):
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(scheduler.put_sched_config({"owner_birthday": "02-30"}, auth=None))
    assert exc.value.status_code == 422


def test_put_owner_birthday_accepts_valid_mm_dd(monkeypatch):
    saved = {}
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: saved.update(cfg))
    asyncio.run(scheduler.put_sched_config({"owner_birthday": "04-24"}, auth=None))
    assert saved["owner_birthday"] == "04-24"


def test_owner_status_reports_unconfigured_when_missing():
    status = scheduler.owner_status({"scheduler": {}})
    assert status["configured"] is False
    assert status["owner_birthday_set"] is False


def test_owner_status_reports_configured_and_masks_invalid_birthday():
    status = scheduler.owner_status({"scheduler": {"owner_id": "123456", "owner_birthday": "MM-DD"}})
    assert status["configured"] is True
    assert status["owner_id"] == "123456"
    assert status["owner_birthday"] == ""
    assert status["owner_birthday_set"] is False


def test_owner_status_illegal_owner_id_is_not_configured():
    status = scheduler.owner_status({"scheduler": {"owner_id": "张三"}})
    assert status["configured"] is False
