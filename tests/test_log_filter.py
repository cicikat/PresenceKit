"""Tests for admin.log_filter noise-suppression filters."""

import logging
import sys

import pytest

from admin.log_filter import (
    _IgnoreWin10054ProactorFilter,
    install_asyncio_proactor_noise_filter,
)

_MSG = "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"


def _make_record(name: str, msg: str, exc: BaseException | None = None) -> logging.LogRecord:
    record = logging.LogRecord(
        name=name,
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=(type(exc), exc, None) if exc else None,
    )
    return record


def _win10054() -> ConnectionResetError:
    err = ConnectionResetError("[WinError 10054] 远程主机强迫关闭了一个现有的连接。")
    err.winerror = 10054
    return err


# ── filter=False means suppressed ────────────────────────────────────────────

def test_suppresses_exact_winerror_10054():
    f = _IgnoreWin10054ProactorFilter()
    record = _make_record("asyncio", _MSG, _win10054())
    assert f.filter(record) is False


# ── filter=True means passes through ─────────────────────────────────────────

def test_passes_asyncio_error_without_exc_info():
    f = _IgnoreWin10054ProactorFilter()
    record = _make_record("asyncio", _MSG, exc=None)
    assert f.filter(record) is True


def test_passes_asyncio_connection_reset_without_winerror():
    f = _IgnoreWin10054ProactorFilter()
    err = ConnectionResetError("plain reset, no winerror attribute")
    record = _make_record("asyncio", _MSG, err)
    assert f.filter(record) is True


def test_passes_asyncio_connection_reset_wrong_winerror():
    f = _IgnoreWin10054ProactorFilter()
    err = ConnectionResetError()
    err.winerror = 10053  # WSAECONNABORTED — different code
    record = _make_record("asyncio", _MSG, err)
    assert f.filter(record) is True


def test_passes_asyncio_other_exception_type():
    f = _IgnoreWin10054ProactorFilter()
    err = OSError("unrelated os error")
    record = _make_record("asyncio", _MSG, err)
    assert f.filter(record) is True


def test_passes_asyncio_different_message():
    f = _IgnoreWin10054ProactorFilter()
    record = _make_record("asyncio", "some other asyncio error", _win10054())
    assert f.filter(record) is True


def test_passes_non_asyncio_logger():
    f = _IgnoreWin10054ProactorFilter()
    record = _make_record("uvicorn", _MSG, _win10054())
    assert f.filter(record) is True


def test_passes_root_logger():
    f = _IgnoreWin10054ProactorFilter()
    record = _make_record("root", _MSG, _win10054())
    assert f.filter(record) is True


def test_install_attaches_filter_to_asyncio_logger():
    asyncio_logger = logging.getLogger("asyncio")
    before = len(asyncio_logger.filters)
    install_asyncio_proactor_noise_filter()
    assert len(asyncio_logger.filters) == before + 1
    # cleanup so repeated test runs don't stack filters
    asyncio_logger.filters.pop()
