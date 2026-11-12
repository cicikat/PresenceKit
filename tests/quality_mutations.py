"""Opt-in pytest plugin: bounded in-memory mutations, never edits source files."""

import importlib
import inspect
import textwrap
import types

import pytest


MUTATIONS = {
    "gmail_secret": ("core.mail.gmail", "config_status",
                     '"client_id": cfg.client_id,', '"client_secret": cfg.client_secret, "client_id": cfg.client_id,',
                     "tests/test_gmail_oauth.py::test_config_api_is_admin_only_and_never_echoes_secrets"),
    "gmail_role": ("core.mail.gmail", "bound",
                   '(record.get("uid"), record.get("char_id")) == (uid, char_id)',
                   'record.get("uid") == uid',
                   "tests/test_gmail_oauth.py::test_browser_state_account_refresh_and_revoke"),
    "duplicate_effect": ("core.owner_turn_service", "execute_idempotent_owner_turn",
                         'row = owner_turn_receipts.load(context.token_label, client_turn_id)', 'row = None',
                         "tests/test_brief171_owner_turn_diary.py::test_owner_turn_receipt_is_serial_and_conflict_safe"),
    "disabled_send": ("core.turn_sink", "_fanout",
                      'targets = [ch for ch in targets if enabled(ch.name)]', 'targets = list(targets)',
                      "tests/test_turn_sink.py::test_disabled_channel_cannot_send_even_when_active"),
    "raw_protocol": ("core.tool_grounding", "detect_tool_meta_leak",
                     'def detect_tool_meta_leak(', 'def detect_tool_meta_leak(',
                     "tests/test_tool_grounding.py::test_detect_tool_meta_leak_matches_discovery_fixed_phrases"),
}


def pytest_addoption(parser):
    parser.addoption("--quality-mutation", choices=tuple(MUTATIONS))


@pytest.fixture(autouse=True)
def apply_quality_mutation(request, monkeypatch):
    name = request.config.getoption("--quality-mutation")
    if not name:
        return
    module_name, function_name, old, new, _ = MUTATIONS[name]
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    source = textwrap.dedent(inspect.getsource(function))
    assert source.count(old) == 1, f"mutation target drifted: {name}"
    if name == "raw_protocol":
        import ast
        tree = ast.parse(source)
        tree.body[0].body = [ast.Return(value=ast.Constant(value=False))]
        source = ast.unparse(ast.fix_missing_locations(tree))
    else:
        source = source.replace(old, new, 1)
    namespace = dict(function.__globals__)
    exec(compile(source, f"<quality-mutation:{name}>", "exec"), namespace)
    replacement = namespace[function_name]
    patched = types.FunctionType(replacement.__code__, function.__globals__, function_name,
                                 replacement.__defaults__)
    patched.__kwdefaults__ = replacement.__kwdefaults__
    monkeypatch.setattr(module, function_name, patched)
