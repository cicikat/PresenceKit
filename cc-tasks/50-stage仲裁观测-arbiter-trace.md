# Brief 50 · Stage 仲裁观测：arbiter_trace

> 依赖：无。**建议 Stage 系列最先跑**——51/52/53 都要调 arbiter 行为，没有 trace 全是盲调
> （与记忆侧 Brief 44 同一哲学）。
> 顺带修一个文档债：`docs/stage.md` 引用的 `docs/specs/spec-10-group-chat.md` 在仓库不存在，
> 改为指向实际存在的规格文档或删除该引用。

## 1. 定位

`core/stage/arbiter.py::score_candidates` 已产出 `CandidateScore.parts` 分项，但不落盘。
每轮 Phase A/B 的仲裁决策要能回看：谁参选、各分项多少、谁被选中、为什么。

## 2. 改法

1. `core/stage/runner.py` 每次调用 arbiter 后追加一行到
   `data/runtime/groups/{group_id}/arbiter_trace.jsonl`：
   - 路径经 `core/data_paths.py` 新增 `stage_arbiter_trace(group_id)`（Hard Rule 1）。
   - 写入用 `safe_append_jsonl`，fail-open（写失败 DEBUG 日志，不阻塞轮次）。
2. 行 schema：

```json
{
  "ts": ..., "round_id": "...", "turn_id": "...", "phase": "A" | "B",
  "latest_speaker": "owner", "latest_excerpt": "前40字",
  "addressed": ["char_a"], "candidates": [{"char_id": "...", "total": 0.9, "parts": {...}}],
  "selected": ["char_a"], "chain_depth": 0
}
```

3. 滚动上限照 provenance_log：5MB × 保留 3 份。
4. 只读接口 `GET /group/{id}/arbiter-trace?limit=`（仿 `admin/routers/memory.py`，
   scope 与现有 `/group/*` 读端点一致）。

## 3. 拍板

- `latest_excerpt` 只存前 40 字，不落全文（transcript.json 已有全文，不重复）。
- 不做前端面板，先 API 裸看。

## 4. 测试

1. 一轮 Phase A + 两条 Phase B 链 → trace 恰好 3 行，phase/chain_depth 正确。
2. selected 与 runner 实际发言集合一致。
3. 写失败（mock 磁盘异常）→ 轮次正常完成。
4. 滚动：超 5MB 轮转，最旧份被删。
5. `pytest -n auto tests/test_stage*` + 新增测试文件。

## 5. 不做什么

- 不改任何仲裁逻辑（那是 52/53 的事）。
- 不给 trace 建索引/检索。
