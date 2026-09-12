"""Best-effort image evidence: prose is valid; typed items are optional."""
from __future__ import annotations

import html
import json
import re


class EmptyRecognition(ValueError):
    """The provider returned no readable evidence."""


_LABELS = {'title': '标题', 'note': '描述', 'description': '描述', 'name': '名称',
           'quantity': '数量', 'unit': '单位', 'amount': '金额', 'currency': '币种',
           'occurred_on': '日期', 'items': '明细'}
_METADATA = {'category', 'user_edited_fields', 'captured_at', 'revision', 'id',
             'recognition_status', 'confidence', 'schema_version'}


def clean_description(value: str) -> str:
    """Remove wrappers/control noise, keeping readable evidence and line breaks."""
    text = value[:60000]
    text = re.sub(r'<(think|analysis|script|style)\b[^>]*>.*?(?:</\1>|$)', '', text, flags=re.S | re.I)
    text = re.sub(r'```[^\n]*\n?', '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd\u200b-\u200f\u202a-\u202e\u2066-\u2069]', '', text)
    text = re.sub(r'"?(?:' + '|'.join(_METADATA) + r')"?\s*:\s*(?:\[[^\]]*\]|[^,\n}]+),?', '', text)
    for key, label in _LABELS.items():
        text = re.sub(r'(?<!\w)["\']?' + key + r'["\']?\s*:', label + '：', text)
    text = re.sub(r'"[A-Za-z_][A-Za-z_0-9]*"\s*:', '', text)
    text = re.sub(r'\b(?:null|None|NaN|undefined)\b', '', text)
    text = re.sub(r'[{}\[\]`]', '', text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r'[ \t]+', ' ', line).strip(' ,"\t')
        if line and re.search(r'[^\W_]', line, re.UNICODE):
            lines.append(line)
    return '\n'.join(lines)[:20000].strip()


def _readable(value, depth=0):
    if depth > 8 or value is None or isinstance(value, bool):
        return ''
    if isinstance(value, dict):
        parts = []
        for key, item in list(value.items())[:300]:
            if key in _METADATA:
                continue
            rendered = _readable(item, depth + 1)
            if rendered:
                parts.append((_LABELS[key] + '：' if key in _LABELS else '') + rendered)
        return '\n'.join(parts)
    if isinstance(value, list):
        return '\n'.join(filter(None, (_readable(item, depth + 1) for item in value[:300])))
    return str(value)


def extract(output: str) -> dict:
    """Never reject useful prose because one optional JSON field is malformed."""
    if not isinstance(output, str):
        raise EmptyRecognition('empty_recognition')
    text = output.strip()[:60000]
    if text == '[OCR: no text detected]':
        raise EmptyRecognition('empty_recognition')
    candidate = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I)
    try:
        parsed = json.loads(candidate)
    except (ValueError, RecursionError):
        parsed = None
    description = clean_description(_readable(parsed) if parsed is not None else text)
    if not description:
        raise EmptyRecognition('empty_recognition')
    result = {'recognition_description': description, 'recognition_format': 'description'}
    if isinstance(parsed, dict):
        title = parsed.get('title')
        if isinstance(title, str) and clean_description(title):
            result['title'] = clean_description(title)[:120]
        # Validate fields independently. Unusable values remain in the description.
        # Keep this aligned with the existing mobile editor's decimal limits.
        items = []
        for item in (parsed.get('items') or [])[:100] if isinstance(parsed.get('items'), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str):
                continue
            name = clean_description(item['name'])[:120]
            if not name:
                continue
            row = {'name': name}
            for key in ('quantity', 'amount'):
                value = item.get(key)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    value = str(value)
                    if re.fullmatch(r'-?\d{1,12}(?:\.\d{1,6})?', value) and (key != 'quantity' or float(value) > 0):
                        row[key] = value
            unit, currency = item.get('unit'), item.get('currency')
            if isinstance(unit, str):
                row['unit'] = clean_description(unit)[:32]
            if isinstance(currency, str) and re.fullmatch('[A-Za-z]{3}', currency):
                row['currency'] = currency.upper()
            if 'currency' not in row:
                row.pop('amount', None)
            items.append(row)
        if items:
            result['items'] = items
            result['recognition_format'] = 'items_and_description'
    return result
