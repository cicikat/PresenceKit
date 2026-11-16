"""Tool validation; called through the stable dispatcher facade."""

def _schema_value_matches(value, expected: object) -> bool:
    from core import tool_dispatcher as _runtime
    if isinstance(expected, list):
        return any(_runtime._schema_value_matches(value, item) for item in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _schema_errors(value, schema: object, path: str = "arguments") -> list[str]:
    """Validate the JSON Schema subset used by built-in and MCP tools."""
    from core import tool_dispatcher as _runtime
    if not isinstance(schema, dict):
        return []
    expected = schema.get("type")
    if expected is not None and not _runtime._schema_value_matches(value, expected):
        return [f"{path} 类型不符合 schema"]
    if "enum" in schema and value not in schema.get("enum", []):
        return [f"{path} 不在允许值中"]
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list) and alternatives:
        if not any(not _runtime._schema_errors(value, candidate, path) for candidate in alternatives):
            return [f"{path} 不符合任一允许结构"]
    if isinstance(value, dict):
        required = schema.get("required") or []
        missing = [key for key in required if isinstance(key, str) and key not in value]
        if missing:
            return [f"{path} 缺少 {missing[0]}"]
        properties = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                return [f"{path}.{unknown[0]} 未在 schema 中声明"]
        for key, child_schema in properties.items():
            if key in value:
                errors = _runtime._schema_errors(value[key], child_schema, f"{path}.{key}")
                if errors:
                    return errors
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors = _runtime._schema_errors(item, schema["items"], f"{path}[{index}]")
            if errors:
                return errors
    return []
