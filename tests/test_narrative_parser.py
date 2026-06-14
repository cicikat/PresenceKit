"""
tests/test_narrative_parser.py — Phase 1 parser unit tests.

Requirement coverage (numbered per spec):
  1. 纯文本 → 一个 narration segment，content 等于原文
  2. <say>你好</say> → say segment，content 为"你好"
  3. 裸文本 + <say> 混合 → narration + say
  4. <do> / <env> / <feel> 均可识别
  5. 未知标签不丢内容
  6. 未闭合标签不抛异常
  7. 空标签不产生脏 segment
  8. 多段同类标签顺序保留
  9. 解析异常 fallback 不影响 content
"""

import pytest
from core.narrative_parser import parse_narrative_segments


# ── helpers ──────────────────────────────────────────────────────────────────

def _types(result):
    return [s["type"] for s in result["segments"]]


def _texts(result):
    return [s["text"] for s in result["segments"]]


def _all_text(result):
    return " ".join(s["text"] for s in result["segments"])


# ═══════════════════════════════════════════════════════════════════════════════
# Case 1 — 纯文本
# ═══════════════════════════════════════════════════════════════════════════════

def test_plain_text_single_narration_segment():
    # Markdown 路径下纯文本视为 say（对白），内容不丢
    r = parse_narrative_segments("hello world")
    assert len(r["segments"]) == 1
    assert r["segments"][0]["text"] == "hello world"
    assert r["content"] == "hello world"


def test_plain_text_chinese():
    # Markdown 路径下纯文本视为 say（对白）
    r = parse_narrative_segments("她低着头，沉默了很久。")
    assert len(r["segments"]) == 1
    assert r["content"] == "她低着头，沉默了很久。"


# ═══════════════════════════════════════════════════════════════════════════════
# Case 2 — 单个 <say> 标签
# ═══════════════════════════════════════════════════════════════════════════════

def test_say_tag_segment_type_and_text():
    r = parse_narrative_segments("<say>你好</say>")
    assert _types(r) == ["say"]
    assert r["segments"][0]["text"] == "你好"


def test_say_tag_content_stripped():
    r = parse_narrative_segments("<say>你好</say>")
    assert r["content"] == "你好"
    assert "<say>" not in r["content"]
    assert "</say>" not in r["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# Case 3 — 裸文本 + <say> 混合
# ═══════════════════════════════════════════════════════════════════════════════

def test_mixed_narration_and_say_order():
    r = parse_narrative_segments("她抬起头，<say>你在哪里？</say>声音很轻。")
    assert _types(r) == ["narration", "say", "narration"]


def test_mixed_say_text_content():
    r = parse_narrative_segments("她说：<say>再见</say>，然后走了。")
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    assert say_segs[0]["text"] == "再见"
    assert "<" not in r["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# Case 4 — do / env / feel 均可识别
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tag", ["do", "env", "feel"])
def test_each_known_tag_recognized(tag):
    r = parse_narrative_segments(f"<{tag}>test content</{tag}>")
    assert len(r["segments"]) == 1
    assert r["segments"][0]["type"] == tag
    assert r["segments"][0]["text"] == "test content"


def test_all_four_known_tags_together():
    reply = "<say>对白</say><do>动作</do><env>环境</env><feel>感受</feel>"
    r = parse_narrative_segments(reply)
    assert _types(r) == ["say", "do", "env", "feel"]
    # content has no markup
    assert "<" not in r["content"]
    assert "对白" in r["content"]
    assert "感受" in r["content"]


# ═══════════════════════════════════════════════════════════════════════════════
# Case 5 — 未知标签不丢内容
# ═══════════════════════════════════════════════════════════════════════════════

def test_unknown_tag_content_not_lost_in_segments():
    r = parse_narrative_segments("<unknown>some text</unknown>")
    assert "some text" in _all_text(r)


def test_unknown_tag_content_not_lost_in_content():
    r = parse_narrative_segments("<mystery>hidden content</mystery>")
    assert "hidden content" in r["content"]


def test_unknown_tag_produces_no_own_type_segment():
    r = parse_narrative_segments("<xyz>stuff</xyz>")
    for seg in r["segments"]:
        assert seg["type"] != "xyz"


def test_unknown_tag_mixed_with_known():
    r = parse_narrative_segments("<say>hello</say><br/>world")
    # "hello" in say segment, "world" in narration, "<br/>" treated as unknown
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    assert say_segs[0]["text"] == "hello"
    combined = _all_text(r)
    assert "world" in combined


# ═══════════════════════════════════════════════════════════════════════════════
# Case 6 — 未闭合标签不抛异常
# ═══════════════════════════════════════════════════════════════════════════════

def test_unclosed_known_tag_no_exception():
    # Must not raise
    r = parse_narrative_segments("<say>没有闭合")
    assert isinstance(r, dict)
    assert "content" in r
    assert "segments" in r


def test_unclosed_tag_content_preserved():
    r = parse_narrative_segments("<say>对白没闭合")
    assert "对白没闭合" in _all_text(r)


def test_unclosed_tag_text_accessible():
    r = parse_narrative_segments("前缀 <feel>内心独白没闭合")
    combined = _all_text(r)
    assert "内心独白没闭合" in combined


# ═══════════════════════════════════════════════════════════════════════════════
# Case 7 — 空标签不产生脏 segment
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_known_tag_no_segment():
    r = parse_narrative_segments("<say></say>")
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    assert say_segs == []


def test_whitespace_only_tag_no_segment():
    r = parse_narrative_segments("<do>   </do>")
    do_segs = [s for s in r["segments"] if s["type"] == "do"]
    assert do_segs == []


def test_all_segments_have_nonempty_text():
    r = parse_narrative_segments("<say></say><do>  </do><env>content</env>")
    for seg in r["segments"]:
        assert seg["text"].strip() != ""


# ═══════════════════════════════════════════════════════════════════════════════
# Case 8 — 多段同类标签顺序保留
# ═══════════════════════════════════════════════════════════════════════════════

def test_multiple_same_tag_order_preserved():
    r = parse_narrative_segments("<say>A</say><say>B</say><say>C</say>")
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    assert [s["text"] for s in say_segs] == ["A", "B", "C"]


def test_interleaved_tags_order_preserved():
    r = parse_narrative_segments("<say>说话</say><do>动作</do><say>再说</say>")
    assert _types(r) == ["say", "do", "say"]


def test_many_segments_relative_order():
    reply = "叙述1 <say>S1</say> 叙述2 <do>D1</do> 叙述3"
    r = parse_narrative_segments(reply)
    types = _types(r)
    # narration → say → narration → do → narration
    assert types.index("say") < types.index("do")


# ═══════════════════════════════════════════════════════════════════════════════
# Case 9 — 解析异常 fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_exception_fallback_content_equals_reply(monkeypatch):
    import core.narrative_parser as _mod

    def _raise(_reply):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(_mod, "_parse", _raise)

    raw = "fallback text"
    r = parse_narrative_segments(raw)
    assert r["content"] == raw


def test_exception_fallback_single_narration_segment(monkeypatch):
    import core.narrative_parser as _mod

    def _raise(_reply):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(_mod, "_parse", _raise)

    raw = "fallback text"
    r = parse_narrative_segments(raw)
    assert len(r["segments"]) == 1
    assert r["segments"][0]["type"] == "narration"
    assert r["segments"][0]["text"] == raw


# ═══════════════════════════════════════════════════════════════════════════════
# Extra edge cases
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_string_returns_no_segments():
    r = parse_narrative_segments("")
    assert r["content"] == ""
    assert r["segments"] == []


def test_original_reply_not_mutated():
    raw = "<say>不变</say>"
    parse_narrative_segments(raw)
    assert raw == "<say>不变</say>"


def test_content_has_no_angle_brackets():
    r = parse_narrative_segments("前 <say>说</say> 后 <do>做</do>")
    assert "<" not in r["content"]
    assert ">" not in r["content"]


def test_multiline_reply():
    reply = "她走进房间。\n<say>你好啊。</say>\n<feel>心里有点紧张。</feel>"
    r = parse_narrative_segments(reply)
    types = _types(r)
    assert "narration" in types
    assert "say" in types
    assert "feel" in types


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2A — Chat 模式最小验收（prompt 指令 + parser 联通）
# ═══════════════════════════════════════════════════════════════════════════════

def test_phase2a_say_only_valid():
    """用户说"你好" → 模型可仅输出 <say>你好。</say>"""
    r = parse_narrative_segments("<say>你好。</say>")
    assert r["segments"] == [{"type": "say", "text": "你好。"}]


def test_phase2a_do_plus_say_valid():
    """可观察动作 + 对白组合合法"""
    r = parse_narrative_segments("<do>他轻轻点头。</do> <say>早安。</say>")
    assert _types(r) == ["do", "say"]
    assert r["segments"][0]["text"] == "他轻轻点头。"
    assert r["segments"][1]["text"] == "早安。"


def test_phase2a_natural_language_valid():
    """Markdown 路径下自然语言输出解析为 say（对白），内容不丢"""
    r = parse_narrative_segments("早安。")
    assert len(r["segments"]) == 1
    assert r["segments"][0]["text"] == "早安。"
    assert r["content"] == "早安。"


def test_phase2a_chat_style_instruction_in_prompt():
    """chat 风格指令使用 Markdown 格式（*do*），不再使用 XML <say>/<do> 标签。"""
    import pathlib
    src = pathlib.Path("core/prompt_builder.py").read_text(encoding="utf-8")
    assert '"chat":' in src or "'chat':" in src

    chat_block_start = src.find('"chat": (')
    chat_block_end = src.find("),", chat_block_start)
    chat_instruction = src[chat_block_start:chat_block_end]

    # 应包含 chat 模式的核心输出规则说明
    assert "对白" in chat_instruction or "Chat" in chat_instruction, (
        "chat 指令应包含 Chat 模式的输出规则说明"
    )
    # 不应再使用 XML 标签作为格式指引
    assert "<say>" not in chat_instruction, "chat 指令不应使用 XML <say> 标签"
    assert "<do>" not in chat_instruction, "chat 指令不应使用 XML <do> 标签"
    # <env>/<feel> 仍不应在 chat 指令中被推荐
    assert "优先用 <env>" not in chat_instruction
    assert "可以用 <env>" not in chat_instruction
    assert "优先用 <feel>" not in chat_instruction
    assert "可以用 <feel>" not in chat_instruction
    assert "严禁出现任何动作描写" not in chat_instruction


# ═══════════════════════════════════════════════════════════════════════════════
# Markdown 协议 — Phase 1B（新格式兼容）
# ═══════════════════════════════════════════════════════════════════════════════

def test_md_do_asterisk_single_line():
    r = parse_narrative_segments("*他低下头，轻轻碰了碰你的额头。*")
    assert _types(r) == ["do"]
    assert r["segments"][0]["text"] == "他低下头，轻轻碰了碰你的额头。"


def test_md_do_not_bold():
    """**text** 不应被识别为 do。"""
    r = parse_narrative_segments("**加粗文字**")
    assert all(s["type"] != "do" for s in r["segments"])


def test_md_feel_underscore():
    r = parse_narrative_segments("_他忽然觉得心口很安静。_")
    assert _types(r) == ["feel"]
    assert r["segments"][0]["text"] == "他忽然觉得心口很安静。"


def test_md_feel_not_double_underscore():
    """__text__ 不应被识别为 feel。"""
    r = parse_narrative_segments("__下划线__")
    assert all(s["type"] != "feel" for s in r["segments"])


def test_md_env_blockquote():
    r = parse_narrative_segments("> 夜色很低，窗外的风声轻轻压下来。")
    assert _types(r) == ["env"]
    assert r["segments"][0]["text"] == "夜色很低，窗外的风声轻轻压下来。"


def test_md_plain_text_is_say():
    r = parse_narrative_segments("你还好吗？")
    assert _types(r) == ["say"]
    assert r["segments"][0]["text"] == "你还好吗？"


def test_md_mixed_all_types():
    reply = "*他走近，低下头。*\n你还好吗？\n_他忽然觉得这一刻很安静。_\n> 窗外的风声轻轻压下来。"
    r = parse_narrative_segments(reply)
    assert _types(r) == ["do", "say", "feel", "env"]


def test_md_multiline_say_accumulated():
    """连续普通文本行合并为一个 say segment。"""
    reply = "你还好吗？\n这几天怎么样。"
    r = parse_narrative_segments(reply)
    assert len(r["segments"]) == 1
    assert r["segments"][0]["type"] == "say"
    assert "你还好吗？" in r["segments"][0]["text"]
    assert "这几天怎么样。" in r["segments"][0]["text"]


def test_md_empty_line_flushes_say():
    """空行分隔两段对白，产生两个 say segments。"""
    reply = "第一句。\n\n第二句。"
    r = parse_narrative_segments(reply)
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    assert len(say_segs) == 2


def test_md_inline_asterisk_not_do():
    """行内 *强调* 不独占整行，不应被识别为 do。"""
    r = parse_narrative_segments("他说，*悄悄地*，我不知道。")
    assert all(s["type"] != "do" for s in r["segments"])


def test_md_inline_underscore_not_feel():
    """行内 _下划线_ 不独占整行，不应被识别为 feel。"""
    r = parse_narrative_segments("变量_name_here")
    assert all(s["type"] != "feel" for s in r["segments"])


def test_md_content_strips_markers():
    reply = "*他走近。*\n你好。\n_心里平静。_\n> 风声。"
    r = parse_narrative_segments(reply)
    assert "他走近。" in r["content"]
    assert "你好。" in r["content"]
    assert "心里平静。" in r["content"]
    assert "风声。" in r["content"]
    assert "*" not in r["content"]
    assert "_" not in r["content"]


def test_md_old_xml_still_works():
    """旧 XML 格式继续正常解析，不受 Markdown 路径影响。"""
    r = parse_narrative_segments("<say>你好</say><do>动作</do>")
    assert _types(r) == ["say", "do"]
    assert r["segments"][0]["text"] == "你好"
    assert r["segments"][1]["text"] == "动作"


def test_md_empty_asterisks_no_segment():
    """** 中间为空，不产生 do 段落。"""
    r = parse_narrative_segments("**")
    assert all(s["type"] != "do" for s in r["segments"])


def test_md_empty_string_no_segments():
    r = parse_narrative_segments("")
    assert r["segments"] == []
    assert r["content"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# CC-06 — Inline style tags (hl / big / sm)
# ═══════════════════════════════════════════════════════════════════════════════

def test_inline_hl_preserved_in_say_segment_text():
    r = parse_narrative_segments("<say>我<hl>很</hl>想你</say>")
    assert _types(r) == ["say"]
    assert "<hl>很</hl>" in r["segments"][0]["text"]


def test_inline_hl_stripped_from_content():
    r = parse_narrative_segments("<say>我<hl>很</hl>想你</say>")
    assert r["content"] == "我很想你"
    assert "<hl>" not in r["content"]
    assert "</hl>" not in r["content"]


def test_inline_big_preserved_in_say_segment_text():
    r = parse_narrative_segments("<say>真的<big>很</big>重要</say>")
    assert "<big>很</big>" in r["segments"][0]["text"]


def test_inline_sm_preserved_in_say_segment_text():
    r = parse_narrative_segments("<say>（<sm>小声</sm>）就这样</say>")
    assert "<sm>小声</sm>" in r["segments"][0]["text"]


def test_inline_tags_content_stripped():
    """content 字段对 hl/big/sm 全剥，只保留纯文本。"""
    r = parse_narrative_segments("<say>你<big>好</big>啊，<hl>真的</hl>。</say>")
    assert "<big>" not in r["content"]
    assert "<hl>" not in r["content"]
    assert "好" in r["content"]
    assert "真的" in r["content"]


def test_inline_hl_not_treated_as_segment_boundary():
    """<hl> 是段内标签，不应切分出新的 segment 类型。"""
    r = parse_narrative_segments("<say>正文<hl>词</hl>更多</say>")
    assert _types(r) == ["say"]
    assert r["segments"][0]["text"] == "正文<hl>词</hl>更多"


def test_non_whitelist_unknown_tag_stripped_from_segment_text():
    """<think> 等非白名单未知标签从 segment.text 中剥除，但文字内容保留。"""
    r = parse_narrative_segments("<say>正文<think>内心</think>结尾</say>")
    seg_text = r["segments"][0]["text"]
    assert "<think>" not in seg_text
    assert "内心" in seg_text  # text content preserved
    assert "结尾" in seg_text


def test_inline_tags_in_markdown_path_say_preserved():
    """Markdown 路径下 say 段落内的 <hl> 保留在 text，但从 content 剥除。"""
    r = parse_narrative_segments("你<hl>好</hl>啊")
    assert _types(r) == ["say"]
    assert "<hl>好</hl>" in r["segments"][0]["text"]
    assert "<hl>" not in r["content"]
    assert "好" in r["content"]


def test_inline_tags_in_mixed_xml_say_and_do():
    """<say> 段含 <hl>，<do> 段不含 inline tag；content 全剥。"""
    r = parse_narrative_segments("<say>我<hl>很</hl>想你</say><do>笑了</do>")
    say_segs = [s for s in r["segments"] if s["type"] == "say"]
    do_segs = [s for s in r["segments"] if s["type"] == "do"]
    assert "<hl>很</hl>" in say_segs[0]["text"]
    assert do_segs[0]["text"] == "笑了"
    assert "<hl>" not in r["content"]
    assert "很" in r["content"]
