"""Pure scenario editor YAML normalization; schema validation stays in scenario_loader."""

import re

_OUTLINE_LIST_FIELDS = frozenset({"exit_signs", "not_yet_allowed", "allowed_hints"})


_OUTLINE_BLOCK_FIELDS = frozenset({"dramatic_task", "entry_pressure", "instruction"})


_OUTLINE_STAGE_FIELDS = frozenset({
    "id", "name", "dramatic_task", "entry_pressure", "exit_signs",
    "not_yet_allowed", "drift_pressure",
})


def _normalize_field_outline_scenario_yaml(yaml_text: str) -> str:
    """Convert the legacy left-aligned scenario outline to canonical YAML.

    This is deliberately not a permissive YAML parser.  It recognizes only the
    scenario fields rendered by the editor and otherwise returns the original
    text for normal YAML parsing and validation.
    """
    lines = yaml_text.splitlines()
    if not any(line.strip() == "private_truths:" for line in lines) or not any(
        line.strip() == "stages:" for line in lines
    ):
        return yaml_text

    document: dict = {"private_truths": [], "stages": []}
    section = "root"
    current_truth: dict | None = None
    current_stage: dict | None = None
    current_rule: dict | None = None
    collecting: tuple[dict, str, str] | None = None

    def fail() -> str:
        return yaml_text

    def append_collected(line: str) -> bool:
        nonlocal collecting
        if collecting is None:
            return False
        target, key, kind = collecting
        if kind == "list":
            if line.strip():
                target[key].append(line.strip().removeprefix("- ").strip())
        else:
            target[key] = f"{target[key]}\n{line}".strip()
        return True

    for raw_line in lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            if collecting and collecting[2] == "block":
                append_collected("")
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s*(.*))?$", raw_line)
        if not match:
            if not append_collected(raw_line):
                return fail()
            continue

        key, value = match.groups()
        value = (value or "").strip()
        if value in {">", "|"}:
            value = ""
        collecting = None
        if key == "private_truths" and not value and section == "root":
            section = "truths"
            continue
        if key == "stages" and not value and section in {"root", "truths"}:
            section = "stages"
            continue
        if section == "root":
            if key not in {"id", "title"} or not value:
                return fail()
            document[key] = value
            continue
        if section == "truths":
            if key == "id" and value:
                current_truth = {"id": value, "disclosure": {}}
                document["private_truths"].append(current_truth)
                current_rule = None
                continue
            if current_truth is None:
                return fail()
            if key == "truth" and value:
                current_truth[key] = value
                continue
            if key == "disclosure" and not value:
                current_rule = None
                continue
            if key == "allowed_hints" and not value and current_rule is not None:
                current_rule[key] = []
                collecting = (current_rule, key, "list")
                continue
            if key == "policy" and value and current_rule is not None:
                current_rule[key] = value
                continue
            if not value:
                current_rule = {}
                current_truth["disclosure"][key] = current_rule
                continue
            return fail()
        if section == "stages":
            if key == "id" and value:
                current_stage = {"id": value}
                document["stages"].append(current_stage)
                current_rule = None
                continue
            if current_stage is None or key not in (_OUTLINE_STAGE_FIELDS - {"id"}) | {"after_turns", "instruction"}:
                return fail()
            if key in _OUTLINE_LIST_FIELDS and not value:
                current_stage[key] = []
                collecting = (current_stage, key, "list")
                continue
            if key == "drift_pressure" and not value:
                current_rule = {}
                current_stage[key] = current_rule
                continue
            if key in {"after_turns", "instruction"} and current_rule is not None:
                if key == "after_turns":
                    if not value.isdigit():
                        return fail()
                    current_rule[key] = int(value)
                else:
                    current_rule[key] = value
                    collecting = (current_rule, key, "block")
                continue
            if key in _OUTLINE_BLOCK_FIELDS:
                current_stage[key] = value
                collecting = (current_stage, key, "block")
                continue
            if key == "name" and value:
                current_stage[key] = value
                continue
            return fail()

    if not document.get("id") or not document.get("title"):
        return yaml_text
    import yaml as _yaml
    return _yaml.safe_dump(document, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _canonical_scenario_yaml(document: dict) -> str:
    import yaml as _yaml

    return _yaml.safe_dump(
        document,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )



class ScenarioAuthoringError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _scenario_document_and_yaml(script_id: str, body: dict) -> tuple[dict, str]:
    document = body.get("document")
    if document is not None:
        if not isinstance(document, dict):
            raise ScenarioAuthoringError(status_code=422, detail="document 必须是 JSON object")
        data = dict(document)
        if data.get("id") and data["id"] != script_id:
            raise ScenarioAuthoringError(status_code=422, detail="document.id 与剧本 id 不一致")
        data["id"] = script_id
        from core.dream.scenario_loader import _validate_script
        try:
            _validate_script(data, require_progressable=True)
        except ValueError as exc:
            raise ScenarioAuthoringError(status_code=422, detail=f"剧本 schema 校验失败: {exc}")
        return data, _canonical_scenario_yaml(data)

    yaml_text = body.get("yaml") or ""
    data = _parse_and_validate_scenario_yaml(script_id, yaml_text)
    return data, _canonical_scenario_yaml(data)

def _parse_and_validate_scenario_yaml(
    script_id: str | None,
    yaml_text: str,
    *,
    require_progressable: bool = False,
) -> dict:
    """解析 + 用剧本加载器的真实 schema 校验，失败返回具体字段错误而不是 500。"""
    import yaml as _yaml
    from core.dream.scenario_loader import _validate_script

    try:
        data = _yaml.safe_load(_normalize_field_outline_scenario_yaml(yaml_text))
    except Exception as e:
        raise ScenarioAuthoringError(status_code=422, detail=f"YAML 解析失败: {e}")
    if not isinstance(data, dict):
        raise ScenarioAuthoringError(status_code=422, detail="剧本必须是 YAML 映射（mapping），不能是列表或标量")
    if script_id is not None and data.get("id") != script_id:
        raise ScenarioAuthoringError(
            status_code=422,
            detail=f"YAML 内 id={data.get('id')!r} 与剧本 id={script_id!r} 不一致",
        )
    try:
        _validate_script(data, require_progressable=require_progressable)
    except ValueError as e:
        raise ScenarioAuthoringError(status_code=422, detail=f"剧本 schema 校验失败: {e}")
    return data


def write_scenario(path, yaml_text):
    from core.safe_write import safe_write_text
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_text(path, yaml_text)


def delete_scenario(path):
    path.unlink()
