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


@pytest.mark.parametrize("newer_correction", [None, "identity", "facts"])
def test_identity_commit_recovers_after_process_exit_without_synthesizing_twice(sandbox,tmp_path,newer_correction):
    """Saving identity then dying before marking evidence must resume one plan."""
    script='''
import asyncio,os,sys,json
from pathlib import Path
from core import sandbox
from core.memory import fixation_pipeline as fp, user_identity as ui, episodic_memory as ep
from core.post_process import slow_queue as sq
paths=sandbox.DataPaths(mode="test",test_session_id="isolated");paths._base=Path(sys.argv[1]);sandbox._instance=paths
async def synthesis(*args,**kwargs):
    with (paths._base/"synthesis-calls.txt").open("a") as stream:stream.write("call;")
    return {"trust_pattern":{"text":"synthetic stable pattern","confidence":.6,"evidence_count":1,"last_updated":100,"counter_evidence_count":0,"last_conflict_at":0.0}},[{"key":"preferred_language","value":"synthetic-language"}]
fp._synthesize_identity=synthesis
async def main():
    if sys.argv[2]=="crash":
        ep._save_memories("owner",[{"id":"synthetic-episode","timestamp":100,"user_said":["synthetic evidence"],"consolidated_at":None}],char_id="fixture_character")
        original=ui.save
        async def interrupted(*args,**kwargs):
            assert await original(*args,**kwargs)
            os._exit(23)
        ui.save=interrupted
        sq.enqueue("consolidate_to_identity",{"uid":"owner","char_id":"fixture_character"})
    sq.register_handler("consolidate_to_identity",fp.handler_consolidate_to_identity)
    sq.start_worker();await sq.drain();await sq.shutdown()
asyncio.run(main())
'''
    for mode in ("crash","recover"):
        result=subprocess.run([sys.executable,"-X","utf8","-c",script,str(tmp_path),mode],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode==(23 if mode=="crash" else 0),result.stderr
        if mode=="crash" and newer_correction:
            import asyncio
            from core.memory import user_identity
            if newer_correction=="identity":
                asyncio.run(user_identity.save("owner",{"trust_pattern":{"text":"newer explicit correction","confidence":1,"evidence_count":1,"last_updated":200}},char_id="fixture_character"))
            else:
                from core.memory import user_facts
                user_facts.save_user_facts("owner",{"preferred_language":"newer explicit fact"},char_id="fixture_character")
    from core.memory import episodic_memory
    episodes=episodic_memory._load_memories("owner",char_id="fixture_character")
    if newer_correction:
        import asyncio
        from core.memory import user_identity
        if newer_correction=="identity":
            identity=asyncio.run(user_identity.load("owner",char_id="fixture_character"))
            assert identity["trust_pattern"]["text"]=="newer explicit correction"
        else:
            from core.memory import user_facts
            assert user_facts.load_user_facts("owner",char_id="fixture_character")["preferred_language"]=="newer explicit fact"
        assert episodes[0].get("consolidated_at") is None
        from core.post_process import memory_outbox
        assert memory_outbox.snapshot()["counts"]["dlq"]==1
    else:
        assert episodes[0].get("consolidated_at") is not None
        from core.memory import user_facts
        assert user_facts.load_user_facts("owner",char_id="fixture_character")["preferred_language"]=="synthetic-language"
    assert (tmp_path/"synthesis-calls.txt").read_text()=="call;"


async def test_durable_identity_task_keeps_isolated_sources_out_of_synthesis(sandbox,monkeypatch):
    from core.memory import episodic_memory,fixation_pipeline as fp,user_identity
    from core.post_process import slow_queue as sq,memory_outbox
    from unittest.mock import AsyncMock
    episodes=[{"id":f"isolated-{n}","source":source,"user_said":["synthetic isolated fact"],"consolidated_at":None}
        for n,source in enumerate(("web","dream_echo","coplay",["user_chat","web"]))]
    episodic_memory._save_memories("owner",episodes,char_id="fixture_character")
    synthesis=AsyncMock(side_effect=AssertionError("isolated evidence reached synthesis"))
    monkeypatch.setattr(fp,"_synthesize_identity",synthesis)
    sq.enqueue("consolidate_to_identity",{"uid":"owner","char_id":"fixture_character"})
    sq.register_handler("consolidate_to_identity",fp.handler_consolidate_to_identity)
    sq.start_worker();await sq.drain();await sq.shutdown()
    assert await user_identity.load("owner",char_id="fixture_character")=={}
    assert await user_identity.load("owner",char_id="another-character")=={}
    assert synthesis.await_count==0
    assert memory_outbox.snapshot()["counts"]=={"done":1}


async def test_identity_evidence_io_failure_reuses_frozen_plan(sandbox,monkeypatch):
    from core.memory import episodic_memory as ep,fixation_pipeline as fp
    from unittest.mock import AsyncMock
    ep._save_memories("owner",[{"id":"synthetic-episode","consolidated_at":None}],char_id="fixture_character")
    synth=AsyncMock(return_value=({"trust_pattern":{"text":"synthetic pattern","confidence":.6,"evidence_count":1,"last_updated":100}},[]))
    monkeypatch.setattr(fp,"_synthesize_identity",synth)
    with monkeypatch.context() as fault:
        fault.setattr(ep,"safe_write_json",lambda *args,**kwargs:False)
        with pytest.raises(RuntimeError,match="identity_evidence_commit_incomplete"):
            await fp.consolidate_to_identity("owner",None,char_id="fixture_character")
    assert ep._load_memories("owner",char_id="fixture_character")[0]["consolidated_at"] is None
    assert await fp.consolidate_to_identity("owner",None,char_id="fixture_character",episode_ids=["synthetic-episode"])
    assert ep._load_memories("owner",char_id="fixture_character")[0]["consolidated_at"] is not None
    assert synth.await_count==1


def test_reflection_recovers_after_episode_write_before_midterm_mark(sandbox,tmp_path):
    """A committed episode must repair its lineage after restart, never mint twice."""
    script='''
import asyncio,os,sys,json,time
from pathlib import Path
from core import sandbox,llm_client
from core.memory import fixation_pipeline as fp,mid_term as mt,episodic_memory as ep
from core.post_process import slow_queue as sq
paths=sandbox.DataPaths(mode="test",test_session_id="isolated");paths._base=Path(sys.argv[1]);sandbox._instance=paths
async def chat(*args,**kwargs):
    with (paths._base/"reflection-calls.txt").open("a") as stream:stream.write("call;")
    return json.dumps({"raw_facts":["synthetic fact"],"topic_keywords":["fixture"],"emotion_peak":"gentle","strength":.5,"narrative_summary":"synthetic reflection"})
llm_client.chat=chat
async def main():
    if sys.argv[2]=="crash":
        mt.append("owner","synthetic summary",mid_id="synthetic-mid",source_turn_id="synthetic-turn",char_id="fixture_character")
        original=ep.write_episode
        def interrupted(*args,**kwargs):
            original(*args,**kwargs);os._exit(23)
        ep.write_episode=interrupted
        sq.enqueue("reflect_to_episodic",{"uid":"owner","char_id":"fixture_character","mid_ids":["synthetic-mid"]})
    sq.register_handler("reflect_to_episodic",fp.handler_reflect_to_episodic)
    sq.start_worker();await sq.drain();await sq.shutdown()
asyncio.run(main())
'''
    for mode in ("crash","recover"):
        result=subprocess.run([sys.executable,"-X","utf8","-c",script,str(tmp_path),mode],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,encoding="utf-8",timeout=30)
        assert result.returncode==(23 if mode=="crash" else 0),result.stderr
    from core.memory import episodic_memory as ep,mid_term as mt,fixation_pipeline as fp
    episodes=ep._load_memories("owner",char_id="fixture_character")
    assert len(episodes)==1
    assert mt.load("owner",char_id="fixture_character")[0].get("promoted_to_episodic_id")==episodes[0]["id"]
    assert fp._load_fixation_state("owner",char_id="fixture_character")["episodic_since_last"]==1
    assert (tmp_path/"reflection-calls.txt").read_text()=="call;"


@pytest.mark.parametrize("projection",["episode","midterm"])
async def test_reflection_retries_failed_projection_without_duplicate_episode(sandbox,monkeypatch,projection):
    from core import llm_client
    from core.memory import episodic_memory as ep,mid_term as mt,fixation_pipeline as fp
    from core.post_process import slow_queue as sq,memory_outbox
    from unittest.mock import AsyncMock
    import json
    mt.append("owner","synthetic summary",mid_id="synthetic-mid",char_id="fixture_character")
    chat=AsyncMock(return_value=json.dumps({"raw_facts":["synthetic fact"],"topic_keywords":["fixture"],"emotion_peak":"gentle","strength":.5,"narrative_summary":"synthetic reflection"}))
    monkeypatch.setattr(llm_client,"chat",chat)
    module=ep if projection=="episode" else mt
    original=module.safe_write_json
    failed=False
    def fail_once(*args,**kwargs):
        nonlocal failed
        if not failed:
            failed=True
            return False
        return original(*args,**kwargs)
    monkeypatch.setattr(module,"safe_write_json",fail_once)
    sq.enqueue("reflect_to_episodic",{"uid":"owner","char_id":"fixture_character","mid_ids":["synthetic-mid"]})
    sq.register_handler("reflect_to_episodic",fp.handler_reflect_to_episodic)
    sq.start_worker();await sq.drain();await sq.shutdown()
    episodes=ep._load_memories("owner",char_id="fixture_character")
    assert len(episodes)==1
    assert mt.load("owner",char_id="fixture_character")[0]["promoted_to_episodic_id"]==episodes[0]["id"]
    assert chat.await_count==(2 if projection=="episode" else 1)
    assert memory_outbox.snapshot()["counts"]=={"done":1}
