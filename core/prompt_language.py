"""Static translations of framework literals, never of authored or user data."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def language() -> str:
    from core.config_loader import get_config
    value = (get_config().get('prompt_language') or 'zh')
    return value if isinstance(value, str) and value in {'zh', 'en'} else 'zh'


@lru_cache(maxsize=1)
def _catalog() -> dict[str, str]:
    path = Path(__file__).parent.parent / 'bundled' / 'i18n' / 'prompt-en.json'
    return json.loads(path.read_text(encoding='utf-8'))


def text(source: str) -> str:
    """Only call with framework-owned literals; placeholders stay untouched."""
    return _catalog().get(source, source) if language() == 'en' else source


def choose(zh: str, en: str) -> str:
    return en if language() == 'en' else zh


@lru_cache(maxsize=1)
def _tools() -> dict:
    path = Path(__file__).parent.parent / 'bundled' / 'i18n' / 'tools-en.json'
    return json.loads(path.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _schema_text() -> dict:
    path = Path(__file__).parent.parent / 'bundled' / 'i18n' / 'schema-en.json'
    return json.loads(path.read_text(encoding='utf-8'))


def tool_description(name: str, source: str) -> str:
    row = _tools().get(name) if language() == 'en' else None
    # A changed or externally supplied definition must not silently inherit an
    # old translation associated with the same name.
    return row['en'] if row and row['source'] == source else source


def tool_parameters(schema: dict, *, tool_name: str | None = None) -> dict:
    """Copy schema annotations only; argument names, enums and values are stable."""
    from copy import deepcopy
    result = deepcopy(schema)
    if language() != 'en':
        return result
    if tool_name is not None and tool_name not in _tools():
        return result
    catalog = _schema_text()

    def visit(node):
        if not isinstance(node, dict):
            return
        if isinstance(node.get('description'), str):
            node['description'] = catalog.get(node['description'], node['description'])
        for key in ('properties', '$defs', 'definitions', 'patternProperties'):
            for child in (node.get(key) or {}).values():
                visit(child)
        for key in ('items', 'additionalProperties', 'not', 'contains', 'if', 'then', 'else'):
            visit(node.get(key))
        for key in ('oneOf', 'anyOf', 'allOf', 'prefixItems'):
            for child in node.get(key) or []:
                visit(child)

    visit(result)
    return result


def tool_schemas(schemas: list[dict] | None) -> list[dict] | None:
    if not schemas or language() != 'en':
        return schemas
    from copy import deepcopy
    result = deepcopy(schemas)
    for item in result:
        function = item.get('function') or item
        function['description'] = tool_description(function.get('name', ''), function.get('description', ''))
        if isinstance(function.get('parameters'), dict):
            function['parameters'] = tool_parameters(function['parameters'], tool_name=function.get('name', ''))
    return result
