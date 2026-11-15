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
