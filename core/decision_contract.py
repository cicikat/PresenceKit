"""Typed closed decisions; text models remain a complete independent backend."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from core.llm_protocol import NormalizedResponse, UpstreamResponseFormatError

# Only consumers that explicitly compile a DecisionRequest may use System One.
SYSTEMONE_CATEGORIES = frozenset({'sensor_judge', 'detect_emotion', 'minecraft_reaction', 'ime_judge', 'probe'})
_LAST: dict[str, dict] = {}


@dataclass(frozen=True)
class Question:
    kind: Literal['choice', 'score', 'noul']
    instructions: str
    criteria: dict[str, str | None] | tuple[str, ...] | None = None
    scale: tuple[float, float] | None = None
    threshold: float | None = None

    def wire(self) -> dict:
        if self.kind == 'choice' and (not isinstance(self.criteria, dict) or not 2 <= len(self.criteria) <= 255):
            raise ValueError('choice requires 2..255 options')
        if self.kind == 'score' and (not isinstance(self.criteria, tuple) or not 2 <= len(self.criteria) <= 10):
            raise ValueError('score requires 2..10 ordered levels')
        if self.kind not in {'choice', 'score', 'noul'} or not self.instructions:
            raise ValueError('invalid decision question')
        result = {'type': self.kind, 'instructions': self.instructions}
        if self.criteria is not None:
            result['criteria'] = list(self.criteria) if isinstance(self.criteria, tuple) else dict(self.criteria)
        return result


@dataclass(frozen=True)
class DecisionRequest:
    task_id: str
    state: Any
    questions: dict[str, Question]
    failure_policy: str = 'fail_closed_drop'
    output: Literal['json', 'label', 'yes_no'] = 'json'
    constants: dict[str, Any] = field(default_factory=dict)
    confidence_gate: float | None = None

    def wire(self, model: str) -> dict:
        if not self.questions or len(self.questions) > 32:
            raise ValueError('decision requires 1..32 questions')
        return {'model': model, 'state': self.state, 'questions': {k: q.wire() for k, q in self.questions.items()}}


@dataclass(frozen=True)
class DecisionResult:
    model: str
    values: dict[str, Any]
    confidences: dict[str, float]
    probabilities: dict[str, Any]
    usage: dict[str, Any]


def endpoint(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme not in {'http', 'https'} or not parts.netloc or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError('System One requires an HTTP base URL without query or fragment')
    base = base_url.rstrip('/')
    if parts.path.rstrip('/').endswith('/systemone'):
        return base
    return base + ('/systemone' if parts.path.rstrip('/').endswith('/v1') else '/v1/systemone')


def _number(value, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError('invalid decision number')
    return float(value)


def parse_response(request: DecisionRequest, body: dict) -> DecisionResult:
    if not isinstance(body, dict) or not isinstance(body.get('model'), str) or not body['model']:
        raise ValueError('missing resolved decision model')
    answers = body.get('answers')
    if not isinstance(answers, dict) or set(answers) != set(request.questions):
        raise ValueError('decision answer IDs do not match questions')
    values, confidences, probabilities = {}, {}, {}
    for key, question in request.questions.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get('type') != question.kind:
            raise ValueError('decision answer type mismatch')
        if question.kind == 'noul':
            value = _number(answer.get('noul'), 0, 1)
            values[key] = value >= question.threshold if question.threshold is not None else value
            probabilities[key] = value
            continue
        confidence = _number(answer.get('confidence'), 0, 1)
        if request.confidence_gate is not None and confidence < request.confidence_gate:
            raise ValueError('decision confidence below gate')
        probs = answer.get('probabilities')
        keys = set(question.criteria) if question.kind == 'choice' else {str(i) for i in range(len(question.criteria))}
        if not isinstance(probs, dict) or set(probs) != keys:
            raise ValueError('invalid decision distribution keys')
        probs = {k: _number(v, 0, 1) for k, v in probs.items()}
        if abs(sum(probs.values()) - 1) > .01:
            raise ValueError('invalid decision distribution sum')
        confidences[key], probabilities[key] = confidence, probs
        if question.kind == 'choice':
            value = answer.get('choice')
            if not isinstance(value, str) or value not in keys:
                raise ValueError('unknown decision choice')
        else:
            value = _number(answer.get('score'), 0, len(question.criteria) - 1)
            if question.scale is not None:
                lo, hi = question.scale
                value = lo + value / (len(question.criteria) - 1) * (hi - lo)
                if isinstance(lo, int) and isinstance(hi, int):
                    value = int(round(value))
        values[key] = value
    usage = body.get('usage') or {}
    if not isinstance(usage, dict):
        raise ValueError('invalid decision usage')
    return DecisionResult(body['model'], values, confidences, probabilities, usage)


def normalize(request: DecisionRequest, result: DecisionResult) -> NormalizedResponse:
    values = {**request.constants, **result.values}
    if request.output == 'label':
        text = str(next(iter(result.values.values())))
    elif request.output == 'yes_no':
        text = 'yes' if next(iter(result.values.values())) else 'no'
    else:
        text = json.dumps(values, ensure_ascii=False)
    # Compatibility projection is produced by our code, never generated prose.
    return NormalizedResponse(text, [], 'completed', result.usage, [], result)


async def create(mc, request: DecisionRequest, *, timeout: float) -> NormalizedResponse:
    from core.no_outbound import assert_outbound_allowed
    assert_outbound_allowed('llm')
    response = await mc.client.post(endpoint(mc.base_url), json=request.wire(mc.model),
        headers={'Authorization': 'Bearer ' + mc.api_key, 'User-Agent': 'PresenceKit/1.0'}, timeout=timeout,
        follow_redirects=False)
    response.raise_for_status()
    try:
        result = parse_response(request, response.json())
        if request.task_id in SYSTEMONE_CATEGORIES or request.task_id == 'detect_affection':
            _LAST[request.task_id] = {'task': request.task_id, 'model': result.model,
                'protocol': 'systemone', 'values': result.values, 'confidence': result.confidences,
                'timestamp': time.time()}
        return normalize(request, result)
    except (TypeError, ValueError, KeyError) as exc:
        raise UpstreamResponseFormatError('invalid System One response', http_status=response.status_code) from exc


def prepare(target, request: DecisionRequest, *, messages: list, gen_kwargs: dict):
    from core.llm_failover import PreparedAttempt
    if getattr(target, 'api_protocol', '') == 'systemone':
        return PreparedAttempt(messages=[], gen_kwargs=gen_kwargs, decision=request)
    from core.prompt_layer import sanitize_messages
    from core.prompt_style import apply_prompt_style
    return PreparedAttempt(messages=sanitize_messages(apply_prompt_style(messages, target.prompt_style)), gen_kwargs=gen_kwargs)


def validate_routes(mp: dict) -> list[str]:
    presets = mp.get('presets') or {}
    native = {name for name, p in presets.items() if p.get('api_protocol') == 'systemone'}
    errors = []
    if mp.get('default_preset') in native:
        errors.append('Jev cannot be the default text preset')
    # A first-preset fallback must remain a text backend as well.
    if presets and next(iter(presets)) in native:
        errors.append('The first preset must remain a text backend for unmapped categories')
    for profile_name, profile in (mp.get('routing_profiles') or {}).items():
        for category, name in profile.items():
            if name in native and category not in SYSTEMONE_CATEGORIES:
                errors.append(f'{profile_name}.{category}: Jev is not supported for this category')
    for profile_name, mapping in (mp.get('fallback_routes') or {}).items():
        for category, name in mapping.items():
            if name in native:
                errors.append(f'{profile_name}.{category}: fallback must be a complete text backend')
    return errors


def snapshot() -> dict:
    return {'scope': 'process', 'recent': list(_LAST.values()), 'persistent_attempts': '/observability/api-calls'}
