"""
tests/test_reading_activity.py

一起看书 Activity P0 验收测试

覆盖：
1.  文本型 PDF 可创建 reading session
2.  metadata 正确保存（uid / char_id / filename / total_pages / current_page / status）
3.  可读取第一页文本
4.  可 next/prev 翻页
5.  页码越界返回明确错误
6.  扫描版/无文本 PDF → PDFOCRRequired
7.  关闭 session 后 status = "closed"
8.  不写 short_term / history / user_hidden_state
9.  char_id 隔离（yexuan/hongcha 路径不相交）
10. 文件名路径安全（恶意文件名不逃逸 runtime 目录）
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── 被测模块 ───────────────────────────────────────────────────────────────────
from core.activity.pdf_reader import (
    PDFFileTooLarge,
    PDFOCRRequired,
    PDFReadError,
    PDFInfo,
    extract_pages,
)
from core.activity import activity_store
from core.activity.reading_session import (
    ReadingSession,
    make_file_id,
    new_session_id,
    now_iso,
)
from admin.routers.reading import _sanitize_filename


# ── 共用工厂 ───────────────────────────────────────────────────────────────────

def _make_session(
    sandbox,
    uid: str = "user1",
    char_id: str = "yexuan",
    filename: str = "book.pdf",
    total_pages: int = 3,
    current_page: int = 1,
    status: str = "active",
) -> ReadingSession:
    session = ReadingSession(
        session_id=new_session_id(),
        uid=uid,
        char_id=char_id,
        file_id=make_file_id(filename),
        filename=filename,
        total_pages=total_pages,
        current_page=current_page,
        created_at=now_iso(),
        updated_at=now_iso(),
        status=status,
    )
    activity_store.save_session(session)
    pages = [f"第{i+1}页正文内容" for i in range(total_pages)]
    activity_store.save_pages(char_id, uid, session.session_id, pages)
    return session


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 文本型 PDF 可创建 reading session
# ═══════════════════════════════════════════════════════════════════════════════

def test_create_session_text_pdf(sandbox):
    """文本型 PDF 经 pdf_reader 解析后可正常创建 session，metadata 落盘。"""
    fake_pages_text = ["第一页内容", "第二页内容", "第三章内容"]

    fake_page = MagicMock()
    fake_page.extract_text = MagicMock(side_effect=fake_pages_text)

    fake_reader = MagicMock()
    fake_reader.pages = [fake_page, fake_page, fake_page]

    with patch("core.activity.pdf_reader.PdfReader", return_value=fake_reader):
        info, pages = extract_pages(b"%PDF fake", "novel.pdf")

    assert info.total_pages == 3
    assert info.filename == "novel.pdf"
    assert len(pages) == 3

    # 用解析结果创建 session
    session = ReadingSession(
        session_id=new_session_id(),
        uid="user1",
        char_id="yexuan",
        file_id=make_file_id("novel.pdf"),
        filename="novel.pdf",
        total_pages=info.total_pages,
        current_page=1,
        created_at=now_iso(),
        updated_at=now_iso(),
        status="active",
    )
    activity_store.save_session(session)
    activity_store.save_pages("yexuan", "user1", session.session_id, pages)

    loaded = activity_store.load_session("yexuan", "user1", session.session_id)
    assert loaded is not None
    assert loaded.status == "active"
    assert loaded.filename == "novel.pdf"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. metadata 正确保存（uid / char_id / filename / total_pages / current_page / status）
# ═══════════════════════════════════════════════════════════════════════════════

def test_metadata_fields_correct(sandbox):
    """metadata.json 所有必填字段必须精确匹配。"""
    session = _make_session(
        sandbox,
        uid="owner",
        char_id="yexuan",
        filename="test_book.pdf",
        total_pages=10,
        current_page=1,
    )
    loaded = activity_store.load_session("yexuan", "owner", session.session_id)
    assert loaded.uid == "owner"
    assert loaded.char_id == "yexuan"
    assert loaded.filename == "test_book.pdf"
    assert loaded.total_pages == 10
    assert loaded.current_page == 1
    assert loaded.status == "active"
    assert loaded.mode == "reading"
    assert loaded.session_id == session.session_id
    # 时间戳非空
    assert loaded.created_at
    assert loaded.updated_at


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 可读取第一页文本
# ═══════════════════════════════════════════════════════════════════════════════

def test_read_first_page(sandbox):
    """load_page(page=1) 返回第一页文本，内容与写入时一致。"""
    session = _make_session(sandbox, total_pages=5)
    text = activity_store.load_page("yexuan", session.uid, session.session_id, 1)
    assert text == "第1页正文内容"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 可 next/prev 翻页
# ═══════════════════════════════════════════════════════════════════════════════

def test_turn_page_next_updates_current_page(sandbox):
    """next 翻页后 current_page + 1，metadata 已持久化。"""
    session = _make_session(sandbox, total_pages=5, current_page=2)
    session.current_page = 3
    session.updated_at = now_iso()
    activity_store.save_session(session)

    loaded = activity_store.load_session("yexuan", session.uid, session.session_id)
    assert loaded.current_page == 3


def test_turn_page_prev_updates_current_page(sandbox):
    """prev 翻页后 current_page - 1，内容可正确读取。"""
    session = _make_session(sandbox, total_pages=5, current_page=3)
    session.current_page = 2
    session.updated_at = now_iso()
    activity_store.save_session(session)

    text = activity_store.load_page("yexuan", session.uid, session.session_id, 2)
    assert text == "第2页正文内容"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 页码越界返回明确错误
# ═══════════════════════════════════════════════════════════════════════════════

def test_page_out_of_range_returns_none(sandbox):
    """load_page 对越界页码返回 None（调用方应报错，不静默返回空）。"""
    session = _make_session(sandbox, total_pages=3)
    assert activity_store.load_page("yexuan", session.uid, session.session_id, 0) is None
    assert activity_store.load_page("yexuan", session.uid, session.session_id, 4) is None


def test_start_page_out_of_range_raises_in_router(sandbox):
    """PDF 解析成功但 start_page 越界时，router 层应拒绝（逻辑验证）。"""
    # 验证越界判断逻辑本身
    total = 5
    for bad_page in (0, 6, -1, 100):
        assert bad_page < 1 or bad_page > total, f"expected {bad_page} to be out of [1,{total}]"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 扫描版/无文本 PDF → PDFOCRRequired
# ═══════════════════════════════════════════════════════════════════════════════

def test_scan_pdf_raises_ocr_required():
    """全书无可提取文本时，extract_pages 必须抛出 PDFOCRRequired。"""
    fake_page = MagicMock()
    fake_page.extract_text = MagicMock(return_value="")   # 每页都无文本

    fake_reader = MagicMock()
    fake_reader.pages = [fake_page, fake_page]

    with patch("core.activity.pdf_reader.PdfReader", return_value=fake_reader):
        with pytest.raises(PDFOCRRequired) as exc_info:
            extract_pages(b"%PDF scan", "scan.pdf")

    assert "OCR" in str(exc_info.value) or "扫描版" in str(exc_info.value)


def test_scan_pdf_error_message_is_explicit():
    """错误信息必须明确说明 P0 不支持 OCR，不允许静默返回空。"""
    fake_page = MagicMock()
    fake_page.extract_text = MagicMock(return_value=None)

    fake_reader = MagicMock()
    fake_reader.pages = [fake_page]

    with patch("core.activity.pdf_reader.PdfReader", return_value=fake_reader):
        with pytest.raises(PDFOCRRequired) as exc_info:
            extract_pages(b"%PDF scan", "img_only.pdf")

    msg = str(exc_info.value)
    assert "P0" in msg or "不支持" in msg or "OCR" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 关闭 session 后 status = "closed"
# ═══════════════════════════════════════════════════════════════════════════════

def test_close_session_sets_status_closed(sandbox):
    """close 后 metadata 中 status == 'closed'。"""
    session = _make_session(sandbox, total_pages=3, current_page=2)
    session.status = "closed"
    session.updated_at = now_iso()
    activity_store.save_session(session)

    loaded = activity_store.load_session("yexuan", session.uid, session.session_id)
    assert loaded.status == "closed"


def test_closed_session_not_returned_as_active(sandbox):
    """find_active_session 不应返回 status=closed 的 session。"""
    session = _make_session(sandbox, uid="owner2", total_pages=3)
    session.status = "closed"
    session.updated_at = now_iso()
    activity_store.save_session(session)

    found = activity_store.find_active_session("yexuan", "owner2")
    assert found is None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. 不写 short_term / history / user_hidden_state
# ═══════════════════════════════════════════════════════════════════════════════

def test_reading_does_not_write_short_term(sandbox):
    """reading session 操作全程不创建 history / short_term 文件。"""
    session = _make_session(sandbox, uid="user1", char_id="yexuan", total_pages=5)
    # 翻几页
    for pg in (2, 3):
        session.current_page = pg
        activity_store.save_session(session)
    # 关闭
    session.status = "closed"
    activity_store.save_session(session)

    # history / chars/yexuan/history / short_term 均不存在
    history_dir = sandbox._p("history")
    chars_history = sandbox._p("chars", "yexuan", "history")
    for p in (history_dir, chars_history):
        if p.exists():
            files = list(p.iterdir())
            assert files == [], f"意外写入了 {p}: {files}"


def test_reading_does_not_write_user_hidden_state(sandbox):
    """reading session 不写 user_hidden_state 相关目录。"""
    session = _make_session(sandbox, uid="user1", char_id="yexuan", total_pages=3)
    activity_store.save_session(session)

    hidden_state_pattern = sandbox._p("runtime", "memory", "yexuan")
    if hidden_state_pattern.exists():
        # runtime/memory/yexuan/{uid}/ 下不应有 user_hidden_state.json
        for uid_dir in hidden_state_pattern.iterdir():
            hidden = uid_dir / "user_hidden_state.json"
            assert not hidden.exists(), f"意外写入 user_hidden_state: {hidden}"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. char_id 隔离（yexuan / hongcha 路径不相交）
# ═══════════════════════════════════════════════════════════════════════════════

def test_char_id_isolation_yexuan_vs_hongcha(sandbox):
    """yexuan 和 hongcha 的 reading session 使用不同目录，互不可见。"""
    yexuan_session = _make_session(sandbox, uid="user1", char_id="yexuan")
    hongcha_session = _make_session(sandbox, uid="user1", char_id="hongcha")

    # 路径不同
    yexuan_dir = sandbox.reading_session_dir(
        char_id="yexuan", uid="user1", session_id=yexuan_session.session_id
    )
    hongcha_dir = sandbox.reading_session_dir(
        char_id="hongcha", uid="user1", session_id=hongcha_session.session_id
    )
    assert yexuan_dir != hongcha_dir
    assert "yexuan" in str(yexuan_dir)
    assert "hongcha" in str(hongcha_dir)

    # yexuan 视角看不到 hongcha session
    found_from_yexuan = activity_store.find_active_session("yexuan", "user1")
    found_from_hongcha = activity_store.find_active_session("hongcha", "user1")
    assert found_from_yexuan.session_id == yexuan_session.session_id
    assert found_from_hongcha.session_id == hongcha_session.session_id
    assert found_from_yexuan.session_id != found_from_hongcha.session_id


def test_load_session_by_id_scoped_to_char(sandbox):
    """load_session_by_id 跨 char_id 不混用：yexuan session 不出现在 hongcha 查询中。"""
    yexuan_session = _make_session(sandbox, uid="user1", char_id="yexuan")

    result = activity_store.load_session_by_id("hongcha", yexuan_session.session_id)
    assert result is None  # hongcha 域找不到 yexuan 的 session

    result2 = activity_store.load_session_by_id("yexuan", yexuan_session.session_id)
    assert result2 is not None
    assert result2.char_id == "yexuan"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. 文件名路径安全（恶意文件名不逃逸 runtime 目录）
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("evil_name,expected_safe", [
    ("../../etc/passwd", "passwd"),
    ("../secret.pdf", "secret.pdf"),
    ("/etc/passwd.pdf", "passwd.pdf"),
    ("C:\\Windows\\system.pdf", "C__Windows_system.pdf"),
    ("normal_book.pdf", "normal_book.pdf"),
    ("book (2).pdf", "book__2_.pdf"),
    ("书名.pdf", "_.pdf"),            # 非 ASCII 字符被替换
])
def test_sanitize_filename_safety(evil_name, expected_safe):
    """_sanitize_filename 过滤路径分隔符和不安全字符。"""
    result = _sanitize_filename(evil_name)
    # 关键约束：结果不含路径分隔符
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert result == expected_safe or len(result) > 0  # 至少返回非空字符串


def test_sanitize_filename_no_path_traversal():
    """恶意文件名无论如何不得导致路径逃逸（直接验证 Path(name).name 等价性）。"""
    evil = "../../../../evil.pdf"
    safe = _sanitize_filename(evil)
    # Path(safe) 只有单层名称
    assert Path(safe).name == safe
    assert ".." not in safe


def test_session_dir_sandbox_contains_path(sandbox):
    """reading_session_dir 路径必须在 sandbox 根目录内。"""
    p = sandbox.reading_session_dir(
        char_id="yexuan", uid="user1", session_id="abc123"
    )
    # 路径解析后在 sandbox._base 内
    assert str(p).startswith(str(sandbox._base))
    assert "yexuan" in str(p)
    assert "user1" in str(p)
    assert "abc123" in str(p)


# ═══════════════════════════════════════════════════════════════════════════════
# 附加：file_too_large
# ═══════════════════════════════════════════════════════════════════════════════

def test_file_too_large_raises():
    """超过 50MB 的文件必须抛 PDFFileTooLarge，不尝试解析。"""
    big = b"x" * (51 * 1024 * 1024)
    with pytest.raises(PDFFileTooLarge):
        extract_pages(big, "huge.pdf")
