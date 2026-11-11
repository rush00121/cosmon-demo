# PartLab agent + eval — how to run

## Dependencies
- macOS arm64, Python 3.14 venv with `mcp` (`python3 -m venv .venv && .venv/bin/pip install mcp`)
- Claude Code CLI (`claude`, logged in with a subscription — no API key). The agent runs as `claude -p` with
  built-in tools disabled; its only tools are the PartLab MCP server (`agent/mcp_server.py`).
- Optional: Docker + local Langfuse (viewer only, see bottom).

## From a clean reset
```bash
./partlab reset
.venv/bin/python -m checks.check_no_hardcoding       # no part names / parameter names / results in logic
.venv/bin/python -m checks.check_nonhappy            # 11 deterministic non-happy-path checks (no LLM)
.venv/bin/python -m agent.run --part bracket_v3      # -> traces/<run_id>/, outputs/bracket_v3.json
.venv/bin/python -m agent.run --part flange_v1
.venv/bin/python -m checks.verify_output outputs/*.json   # oracle: independent re-solve of saved parts
.venv/bin/python -m eval.run_eval traces/ --ground-truth  # -> eval/out/report.md + eval/out/runs/<run>.md
.venv/bin/python -m eval.flawed                      # 13 edited traces must fail exactly their checks
.venv/bin/python -m checks.bad_agents                # 3 real sabotaged agents must be caught (~1% quota)
.venv/bin/python -m checks.scale infra --parts 1000 --jobs 6   # scale, scripted policy, no LLM
.venv/bin/python -m checks.scale llm --parts 30 --jobs 10      # scale, real agents (~10% of 5h window)
```
Each run is isolated in `runs/<run_id>/` (own `.partlab/` session), so runs never share SDK state.
Memory is off by default for clean, independent runs; `--memory on` enables recall/record of lessons
(`memory/lessons.jsonl`), and `eval.learn` turns eval findings into lessons.

## Results (this submission)
| part | outcome | verified by |
|---|---|---|
| bracket_v3 | baseline SF 1.428 → `thickness_mm` 3 → 4, SF 2.198, saved `outputs/bracket_v3.json` | oracle re-solve 2.198; eval PASS |
| flange_v1 | baseline SF 0.212; thickness at 5.0 max → 0.839, hole at 4.0 min → 0.354, fillet no effect → **no_fix_possible**, nothing saved | brute-force sweep agrees; eval PASS |

**Reading a run:** `eval/out/runs/<run>.md` — headline verdict, phases with outcomes, and one row per tool
call: *why (agent)* (intent stated with the call), *why (context)* (derived from workflow state), result, and
any eval finding pinned to that call. Same structure in Langfuse.

**Bad traces are caught** (`eval/out_flawed/report.md`, `eval/out_bad/report.md`; generate with `--ground-truth`):
- 13/13 edited traces fail *exactly* their expected checks (hallucinated SF, change after verify, constraint
  violation, forbidden tool, unshaped log, reduced load, two parameters, no report, retry loop, out-of-order
  calls, oracle disagreement, SDK state divergence, giving up after a recovered error) — exact sets, so a check
  that fires on everything cannot pass.
- 3/3 real sabotaged agents (guards off) caught with step-level evidence: skip verification (saved unverified,
  claimed SF 2.5 vs observed 1.428), ignore constraints (thickness 9 > 5.0 max), give up early (no probes;
  ground truth says fixable). This test also caught an oracle crash on saves without a solve (fixed).

**Non-happy checks:** 11/11 PROVED — every failure becomes a structured tool error with a hint; a
constraint-violating or unverified change cannot reach the SDK or `outputs/`; a false report is rejected live.

**Scale** (`results/`):
| tier | runs | concurrency | throughput | latency p50/p95 | outcome |
|---|---|---|---|---|---|
| infra: scripted policy, full tools/guards/trace/oracle/eval | 1000 synthetic parts | 6 procs | 606 runs/min | 0.6 / 1.5 s | 0 crashes, 0 oracle failures, 1000/1000 statuses = ground truth; eval of 1000 traces (30 MB) incl. ground truth: 159 s |
| real `claude -p` agents | 30 synthetic parts | 10 | 27 runs/min | 20 / 30 s | 30/30 eval PASS, 0 rate-limited, $1.00 API-equiv, 5h window 44%→53% |

What scales: harness, traces and eval are embarrassingly parallel (isolated workdir per run, append-only JSONL,
per-part ground truth in a process pool). The binding constraint for hundreds of real agents is the LLM quota
(~0.3% of a 5-hour subscription window per run → ~300 runs per window), not the harness; beyond that, use an
API key with rate-limit backoff, or a queue spreading runs over windows.

**Supervision:** three independent supervisor-agent audits (code; eval + traces; if/else + hard-coding).
No cheating found; all findings fixed (reduced-load "fixes", part swapping, gameable honest-failure rule,
constraints lost in the foreign adapter, real part values in prompt examples, duplicated constants,
if/elif chains replaced by tables in `agent/workflow.py` and `eval/narrate.py`).

## Layout
`agent/` CLI client · tool layer (guards, shaping, budgets) · `workflow.py` (single workflow table) · MCP
server · prompt · trace · memory · run harness
`checks/` non-happy checks · oracle · ground truth · hard-coding scan · stress · scripted agent · scale · bad agents
`eval/` canonical schema · adapters (our harness, bare Claude Code stream-json) · `config.toml` · checks ·
report · `narrate` (per-call timelines) · `flawed` · `learn` · `export_langfuse`

## Known limitations / unfinished
- **Main limitation:** the fix is not minimised — the agent stops at the first verified value (4 mm).
- Only two trace adapters exist; a Bash-only or OpenAI-SDK harness needs one more adapter (~40 lines).
- Eval thresholds are reasoned defaults, not calibrated against a labeled trace set.
- Memory: no file lock (unsafe for parallel `--memory on` runs), memory tools still offered when memory is off,
  exact-text lesson dedup only.
- Hard-coding scan catches identifiers, not prose (e.g. "thickness"); supervisor review covers prose.

## Optional: Langfuse viewer
Local stack in `~/dev/langfuse-local` (ports bound to 127.0.0.1, random secrets in `.env`, AI-gateway service
removed for Compose 2.5). Export (Langfuse v4: OTLP traces + score API; idempotent):
`LANGFUSE_PUBLIC_KEY=… LANGFUSE_SECRET_KEY=… .venv/bin/python -m eval.export_langfuse traces/ eval/flawed/ eval/bad_traces/ --report eval/out/report.json --report eval/out_flawed/report.json --report eval/out_bad/report.json`
