from core.phone_control.vision_client import _parse_action_payload


def test_parse_continue_tap_with_node_id():
    parsed = _parse_action_payload({
        "status": "continue",
        "action": {"type": "tap", "target_node_id": "n1", "target_point": None},
        "reasoning": "点去结算",
    })
    assert parsed is not None
    assert parsed.status == "continue"
    assert parsed.action["target_node_id"] == "n1"


def test_parse_continue_tap_with_point_fallback():
    parsed = _parse_action_payload({
        "status": "continue",
        "action": {"type": "tap", "target_node_id": None, "target_point": [0.5, 0.8]},
        "reasoning": "图标按钮没有文本，用坐标",
    })
    assert parsed is not None
    assert parsed.action["target_point"] == [0.5, 0.8]


def test_parse_rejects_tap_without_node_or_point():
    parsed = _parse_action_payload({
        "status": "continue",
        "action": {"type": "tap", "target_node_id": None, "target_point": None},
        "reasoning": "没有可执行目标",
    })
    assert parsed is None


def test_parse_type_requires_text():
    missing_text = _parse_action_payload({
        "status": "continue",
        "action": {"type": "type", "target_node_id": "n2"},
        "reasoning": "填数量",
    })
    assert missing_text is None

    ok = _parse_action_payload({
        "status": "continue",
        "action": {"type": "type", "target_node_id": "n2", "text": "2"},
        "reasoning": "填数量",
    })
    assert ok is not None
    assert ok.action["text"] == "2"


def test_parse_scroll_requires_valid_direction():
    invalid = _parse_action_payload({
        "status": "continue",
        "action": {"type": "scroll", "direction": "sideways"},
        "reasoning": "滚动",
    })
    assert invalid is None

    ok = _parse_action_payload({
        "status": "continue",
        "action": {"type": "scroll", "direction": "down"},
        "reasoning": "往下滚动找更多商品",
    })
    assert ok is not None


def test_parse_done_and_refused_force_action_null():
    done = _parse_action_payload({
        "status": "done",
        "action": {"type": "tap", "target_node_id": "n9"},
        "reasoning": "已经到支付确认页",
    })
    assert done is not None
    assert done.action is None

    stopped = _parse_action_payload({
        "status": "refused",
        "action": None,
        "reasoning": "看到支付密码输入框",
        "message": "识别到支付密码页面，已停止",
    })
    assert stopped is not None
    assert stopped.status == "refused"
    assert stopped.message == "识别到支付密码页面，已停止"


def test_parse_refused_defaults_message_when_missing():
    parsed = _parse_action_payload({
        "status": "refused",
        "action": None,
        "reasoning": "不确定",
    })
    assert parsed is not None
    assert parsed.message


def test_parse_rejects_unknown_status():
    assert _parse_action_payload({"status": "maybe", "action": None}) is None


def test_parse_rejects_non_dict():
    assert _parse_action_payload("not a dict") is None
    assert _parse_action_payload(None) is None
