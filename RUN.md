# PartLab agent + eval — how to run

## Dependencies
- macOS arm64, Python 3.14 venv with `mcp` (`python3 -m venv .venv && .venv/bin/pip install mcp`)
- Claude Code CLI (`claude`, logged in with a subscription — no API key). The agent runs as `claude -p` with
  built-in tools disabled; its only tools are the PartLab MCP server (`agent/mcp_server.py`).
- Optional: Docker + local Langfuse (viewer only, see bottom).

## From a clean reset
```bash
./partlab reset
.venv/bin/python -m checks.check_nonhappy            # 11 deterministic non-happy-path checks (no LLM)
.venv/bin/python -m checks.check_no_hardcoding       # no part names / observed results in agent+eval logic
.venv/bin/python -m agent.run --part bracket_v3      # -> traces/<run_id>/, outputs/bracket_v3.json
.venv/bin/python -m agent.run --part flange_v1
.venv/bin/python -m checks.verify_output outputs/*.json   # oracle: independent re-solve of saved parts
.venv/bin/python -m eval.run_eval traces/ --ground-truth  # -> eval/out/report.{md,json}
.venv/bin/python -m eval.flawed                      # deliberately flawed traces must fail exactly their check
.venv/bin/python -m checks.stress --seed 7           # optional: 10 more agent runs (unnamed/synthetic/faults)
```
Each run is isolated in `runs/<run_id>/` (own `.partlab/` session), so runs never share SDK state.

## Results (this submission)
| part | outcome | verified by |
|---|---|---|
| bracket_v3 | baseline SF 1.428 → `thickness_mm` 3 → 4, SF 2.198, saved `outputs/bracket_v3.json` | oracle re-solve 2.198; eval PASS |
| flange_v1 | baseline SF 0.212; best within constraints: thickness 5.0 → 0.839, hole 4.0 → 0.354, fillet no effect → **no_fix_possible**, nothing saved | brute-force sweep agrees; eval PASS |

- Non-happy checks: 11/11 PROVED (constraint guard, injected/layered constraints, invalid transition, CLI usage
  error, save without passing SF, wrong part, reduced load, second parameter, fabricated report, injected faults,
  budget). **What they prove:** every failure becomes a structured tool error with a hint, a constraint-violating
  or unverified change can never reach the SDK or `outputs/`, and a false report is rejected live.
- Stress (seed 7): un-named parts (housing_v1, bracket_v3_thick, stale bracket_v1), 4 synthetic renamed/perturbed
  parts (2 with injected constraints), injected mesh failure + solve timeout, 1-solve budget → all statuses match
  brute-force ground truth; budget run ends in an honest `failed` (eval WARN).
- Eval: 12 memory-off runs → 11 PASS, 1 WARN (budget). Flawed traces: 6/6 flagged exactly
  (`eval/flawed/*`: hallucinated SF, change after verify, constraint violation, forbidden tool, unshaped solve log,
  reduced load); expected failing sets are exact, so a check that fires on everything cannot pass.
- Supervision: two independent supervisor-agent audits (code; then eval + traces). No cheating found; 15 loopholes
  fixed (e.g. reduced-load "fixes", part swapping, recovered-error-then-give-up WARN, constraints lost in the
  foreign adapter). The eval itself was corrected once by a real trace: `synth7_0` probed the SDK's mesher limit
  (hole ≥ 2.0) rather than the nominal bound — now counted as an effective bound when a further value is rejected.
- Learning: `memory/lessons.jsonl` (git). Memory-on demo: run 2 recalled run 1's lesson → 2 solves instead of 3,
  still verified by solve (`traces/*memon-demo*`).

## Layout
`agent/` CLI client · tool layer (guards, shaping, budgets) · MCP server · prompt · trace · memory · run harness
`checks/` non-happy checks · oracle (`verify_output`) · brute-force `ground_truth` · `stress`
`eval/` canonical schema · adapters (our harness, bare Claude Code stream-json) · `config.toml` thresholds ·
checks · report · `flawed` · `learn` (eval findings → lessons) · `export_langfuse`
`traces/<run_id>/harness.jsonl` (tool-level ground truth) + `llm.stream.jsonl` (raw Claude Code stream)

## Known limitations / unfinished
- **Main limitation:** fix is not minimised — the agent stops at the first verified value (e.g. 4 mm), it does
  not search for the smallest passing change; the task does not require it.
- Lesson dedup is exact-text only, so paraphrased observations accumulate (see `memory/lessons.jsonl`).
- Ground truth is a 9-point grid per parameter within bounds (0.25x–4x when unconstrained); a narrow feasible
  window between grid points could be missed.
- Agent loop is owned by `claude -p`, so budgets are enforced in the tool server, not by stopping the loop.

## Optional: Langfuse viewer
Local stack in `~/dev/langfuse-local` (ports bound to 127.0.0.1, random secrets in `.env`, AI-gateway service
removed for Compose 2.5 compatibility). Then:
`LANGFUSE_PUBLIC_KEY=… LANGFUSE_SECRET_KEY=… .venv/bin/python -m eval.export_langfuse traces/ eval/flawed/ --report eval/out/report.json --report eval/out_flawed/report.json`
(Langfuse v4: runs sent as OTLP/JSON to `/api/public/otel/v1/traces`, checks to `/api/public/scores`; one trace
per run, one span per tool call, every eval check as a score; ids derived from trace dir → re-export is idempotent).
