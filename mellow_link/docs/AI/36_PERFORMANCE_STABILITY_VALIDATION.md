# Performance Stability Patches – Validation Checklist

## Env flags (default OFF / safe)

| Flag | Default | Effect |
|------|---------|--------|
| `MELLOW_METRICS_ENABLED` | 0 | Enable metrics collection (TTFT, TPS, TOKENS_*, OBSERVATION_VIOLATION). |
| `MELLOW_METRICS_ASYNC_FLUSH` | 1 | Flush to DB in background; no write on request path. |
| `MELLOW_METRICS_FLUSH_INTERVAL_MS` | 500 | Flush interval in ms. |
| `MELLOW_METRICS_FLUSH_BATCH_SIZE` | 50 | Max events per flush batch. |
| `MELLOW_OBSERVATION_STRICT_MODES` | thinking,research | Modes where finish requires at least one tool Observation. Fast never requires. |
| `MELLOW_PROMPT_TEMPLATE_MODE` | 0 | Use mode-specific mini prompts and section-based assembly (no mid-sentence truncation). |
| `MELLOW_PROMPT_HISTORY_MAX_TURNS_FAST` | 2 | Max recent history turns in fast mode. |
| `MELLOW_PROMPT_HISTORY_MAX_TURNS_THINKING` | 3 | Max recent history turns in thinking/research. |
| `MELLOW_PROMPT_MEMORIES_MAX` | 3 | Max user memory items in prompt. |

---

## 1. Metrics: no request-path DB write when ASYNC_FLUSH enabled

- [ ] Set `MELLOW_METRICS_ENABLED=1`, `MELLOW_METRICS_ASYNC_FLUSH=1`.
- [ ] Run 20+ fast queries (e.g. `/chat/ask` or agent run).
- [ ] Confirm no `INSERT INTO performance_metrics` runs on the request thread (e.g. add a temporary log in `save_metric` and ensure it only appears from a background task or shutdown flush).
- [ ] After a few seconds (or after shutdown), query `performance_metrics` (e.g. via `/api/system/kpis` or DB) and confirm new rows for TTFT_MS, TPS, TOKENS_IN, TOKENS_OUT if streaming was used.

---

## 2. Observation enforcement only in thinking/research

- [ ] **Fast mode:** Run ~20 fast queries that do **not** use tools (e.g. “오늘 날씨 어때?”). No “observation required” or “도구를 한 번 이상 실행한 뒤” failure.
- [ ] **Thinking mode:** Call `run_agent(..., mode="thinking")` with a query that requires filesystem (e.g. “workspace에 있는 파일 목록 알려줘”). If the model tries to `finish` without any tool call:
  - First time: re-prompt (block message).
  - Second time: exit with `finish_reason="observation_required_not_met"` and short user message; one `OBSERVATION_VIOLATION` metric enqueued (visible after flush).

---

## 3. Prompt builder: no mid-sentence truncation

- [ ] Set `MELLOW_PROMPT_TEMPLATE_MODE=1`.
- [ ] Trigger agent run with long context (e.g. many history turns, many memories). Inspect assembled system prompt (log or debug): no policy sentence cut in the middle (e.g. “Observation 결과를 받은 후에만 결” without “론 도출”).
- [ ] Confirm sections are dropped in order: history beyond N turns, memories beyond 3, RAG beyond top 3; never cut a policy sentence in half.

---

## 4. SQLite performance_metrics growth via background flush

- [ ] Enable metrics, run 50+ requests (streaming or agent).
- [ ] Wait at least `MELLOW_METRICS_FLUSH_INTERVAL_MS` (or trigger shutdown).
- [ ] Check `performance_metrics` table: new rows with category in (`TTFT_MS`, `TPS`, `TOKENS_IN`, `TOKENS_OUT`, `OBSERVATION_VIOLATION`).

---

## 5. Rollback (flags off = baseline)

- [ ] Set all new flags to default (metrics off, template mode off). Run the same 20 fast + 10 thinking queries.
- [ ] Behavior matches pre-patch: no metrics in DB (or only existing KPI snapshot), observation required for all modes (if previous behavior was strict) or unchanged; prompt is full template + mission as before.

---

## How to run automated smoke tests

```bash
cd D:\AI_Project
python -m pytest mellow_link/tests/test_performance_stability_patches.py -v
```

- Ensures metrics push does not call DB on the same thread when async flush is used.
- Ensures observation strict modes parsing (fast vs thinking/research).
- Ensures prompt assembler drops whole sections and does not truncate the NO_HALLUCINATION policy mid-sentence.
- Ensures `build_system_prompt(..., use_template_mode=False)` remains backward compatible.
