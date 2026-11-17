"""R3: a process exit cannot discard admitted idempotent memory work."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize("crash_capture", [False, True])
def test_queued_memory_work_survives_process_exit(sandbox, tmp_path, crash_capture):
    script = '''
import asyncio, os, sys
from pathlib import Path
from core import sandbox
from core.post_process import slow_queue as sq
paths = sandbox.DataPaths(mode="test", test_session_id="isolated")
paths._base = Path(sys.argv[1])
sandbox._instance = paths
payload = {"uid":"owner", "char_id":"fixture_character", "turn_id":"recovery", "source":"web", "web_echo":True}
if sys.argv[2] == "enqueue":
    sq.enqueue("summarize_to_midterm", payload)
elif sys.argv[2] == "capture-crash":
    from core.memory import short_term
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import stamp_user_chat
    original = short_term.append
    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        os._exit(23)
    short_term.append = interrupted
    capture_turn("owner", "hello", "reply", turn_id="recovery", char_id="fixture_character", envelope=stamp_user_chat())
else:
    async def handler(data):
        assert data == payload
        if sys.argv[2] == "crash-worker":
            os._exit(23)
        with (paths._base / "recovered.txt").open("a", encoding="utf-8") as stream:
            stream.write(data["turn_id"])
    async def main():
        sq.register_handler("summarize_to_midterm", handler)
        from core.memory.fixation_pipeline import handler_capture_turn_retry
        sq.register_handler("capture_turn_retry", handler_capture_turn_retry)
        sq.start_worker()
        await sq.drain()
        await sq.shutdown()
    asyncio.run(main())
'''
    env = dict(os.environ)
    for mode in (("capture-crash", "recover") if crash_capture else ("enqueue", "crash-worker", "recover", "enqueue", "recover")):
        completed = subprocess.run([sys.executable, "-c", script, str(tmp_path), mode],
            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=20)
        assert completed.returncode == (23 if mode in {"capture-crash", "crash-worker"} else 0), completed.stderr
    if crash_capture:
        from core.memory import short_term, event_log
        history = short_term.load("owner", char_id="fixture_character")
        assert [(m["role"], m["content"]) for m in history] == [("user", "hello"), ("assistant", "reply")]
        text = event_log._full_log_file_write("owner", char_id="fixture_character").read_text(encoding="utf-8")
        assert text.count("turn_id:recovery") == 2
    else:
        assert (tmp_path / "recovered.txt").read_text(encoding="utf-8") == "recovery"


async def test_ledger_failure_is_repaired_without_duplicate_history(sandbox, monkeypatch):
    from core.memory import event_store, event_query, short_term
    from core.memory.scope import MemoryScope
    from core.memory.fixation_pipeline import capture_turn, handler_capture_turn_retry
    from core.post_process import slow_queue as sq, memory_outbox
    from core.write_envelope import stamp_user_chat
    original = event_store.append_event
    monkeypatch.setattr(event_store, "append_event", lambda *a, **k:
        event_store.AppendResult(ok=False, inserted=False, event_id="", error_code="injected_io"))
    assert capture_turn("owner", "hello", "reply", turn_id="repair-ledger", char_id="fixture_character",
                        envelope=stamp_user_chat(), source="web") == "repair-ledger"
    assert memory_outbox.snapshot()["counts"] == {"pending": 1}
    monkeypatch.setattr(event_store, "append_event", original)
    sq.register_handler("capture_turn_retry", handler_capture_turn_retry)
    sq.start_worker()
    await sq.drain()
    await sq.shutdown()
    scope = MemoryScope.reality_scope("owner", "fixture_character")
    assert event_query.get_event(scope, "repair-ledger:user") is None
    assert event_query.get_event(scope, "repair-ledger:user", source="web")["source"] == "web"
    assert len(short_term.load("owner", char_id="fixture_character")) == 2
    assert memory_outbox.snapshot()["counts"] == {"done": 1}


@pytest.mark.parametrize("projection", ["day", "full"])
def test_process_exit_mid_utf8_record_repairs_exact_projection(sandbox, tmp_path, projection):
    """Kill after bytes reach disk, including an incomplete multibyte character.

    Recovery must finish that exact intent, not append a duplicate or leave an
    unreadable Markdown projection. All files belong to this synthetic process.
    """
    script = '''
import asyncio, builtins, os, sys
from pathlib import Path
from core import sandbox
from core.post_process import slow_queue as sq
from core.memory.fixation_pipeline import capture_turn, handler_capture_turn_retry
from core.write_envelope import stamp_user_chat
paths=sandbox.DataPaths(mode="test",test_session_id="isolated")
paths._base=Path(sys.argv[1]);sandbox._instance=paths
if sys.argv[2]=="crash":
    original=builtins.open
    class Partial:
        def __init__(self, stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def write(self,value):
            raw=value.encode("utf-8") if isinstance(value,str) else value
            cut=raw.index(bytes.fromhex("e6b189"))+1
            target=self.stream.buffer if hasattr(self.stream,"buffer") else self.stream
            target.write(raw[:cut]);target.flush();os.fsync(target.fileno());os._exit(23)
    def opened(path,mode="r",*args,**kwargs):
        stream=original(path,mode,*args,**kwargs)
        if mode in {"a","ab"} and (Path(path).name=="full_log.md")== (sys.argv[3]=="full"):
            return Partial(stream)
        return stream
    builtins.open=opened
    capture_turn("owner","synthetic 汉字 evidence","synthetic reply",turn_id="partial-recovery",char_id="fixture_character",envelope=stamp_user_chat())
else:
    async def main():
        sq.register_handler("capture_turn_retry",handler_capture_turn_retry)
        sq.start_worker();await sq.drain();await sq.shutdown()
    asyncio.run(main())
'''
    for mode in ("crash", "recover"):
        result=subprocess.run([sys.executable,"-X","utf8","-c",script,str(tmp_path),mode,projection],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode == (23 if mode=="crash" else 0),result.stderr
    from core.memory import event_log, short_term
    for path in event_log._event_log_write_dir("owner",char_id="fixture_character").glob("*.md"):
        text=path.read_text(encoding="utf-8")
        assert text.count("synthetic 汉字 evidence")==1
        assert text.count("speaker:user turn_id:partial-recovery")==1
        assert text.count("speaker:assistant turn_id:partial-recovery")==1
    assert len(short_term.load("owner",char_id="fixture_character"))==2
    from core.post_process import memory_outbox
    assert memory_outbox.snapshot()["projection_repair"]["pending"]==0
