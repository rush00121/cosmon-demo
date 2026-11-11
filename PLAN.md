# PartLab Agent — Plan v2 (after 3-critic adversarial review)

## Known facts (smoke test + part files)
- `partlab` works (arm64). Stateful: session in `./.partlab/state.json` of the CWD.
- Phases: closed → open → meshed → loaded → solved; `set-parameter` drops to `open`.
- Errors: JSON envelope + exit 1 (`StateError`, …); unknown command / bad args → plaintext usage + exit 2.
- Traps: `solve` embeds ~100-line `log`; `get-solve-log` (large); `compute-stress-from-handbook` (bypasses solve);
  `bracket_v3_thick` (part swap ≠ parameter change); old revisions `is_latest:false`; `get-safety-factor` has `region`
  (use `all`); `mesh` "may fail on invalid geometry"; overrides **accumulate** across trials.
- bracket_v3 baseline SF 1.428 → needs a fix. flange_v1 constraints: thickness ≤ 5.0, hole ≥ 4.0; **fillet unconstrained**.

## LLM backend (decision pending user, default = subscription)
- **Default: `claude -p`** (subscription, no API key) with PartLab tools served by a local **MCP server**,
  `--strict-mcp-config`, built-in tools disabled, only `mcp__partlab__*` allowed → agent cannot reach
  handbook, solve log, Bash, or `parts/*.json`. `--output-format stream-json` = LLM-side trace.
- Alt: Anthropic API key + own loop (critic 2's preference: more control). Backend kept behind one entry point
  (`agent/run.py --backend claude_cli|api`) so swapping is local; only `claude_cli` gets built unless asked.
- Budgets live in the **tool server** (since we don't own the loop): solve cap 12, tool-call cap 60 → tools return
  `BudgetExceeded`, and `claude -p --max-turns`.

## Layout
```
agent/
  partlab_client.py   # subprocess → Envelope(ok,result,error,error_type,exit_code,raw); exit2→UsageError; timeout
  constraints.py      # constraints as data (constraints.json) + check(part, name, value)
  tools.py            # tool handlers: shaping, guards, budgets, base-params tracking (pure, testable w/o LLM)
  mcp_server.py       # thin MCP wrapper over tools.py; writes harness-side trace events
  prompts.py          # system prompt (generic, no part-specific answers)
  trace.py            # JSONL writer, flush per event
  run.py              # per-run isolated workdir → launch claude -p → oracle verify → run_end
checks/
  check_nonhappy.py   # deterministic, no LLM
  verify_output.py    # independent re-solve of outputs/<part>.json (oracle)
eval/                 # Part 2
traces/<run_id>/{harness.jsonl, llm.stream.jsonl}   outputs/   RUN.md
```

## Isolation (critic 2 #5)
Each run/check gets a fresh workdir `runs/<run_id>/` with symlinks to `partlab` + `parts/`; partlab invoked
with that CWD → own `.partlab/`, no cross-talk, reset is automatic. `save-part` gets an absolute path to `outputs/`.

## Tool surface
| tool | behavior |
|---|---|
| `open_part(name)` | name, revision, is_latest, material, default_load; harness records **base params** (via get-parameters) |
| `get_parameters()` | current values + overrides |
| `mesh()` | returns elements, min_quality |
| `apply_load(face_id, force_N, direction)` | direction passed if CLI supports it (discovery) |
| `solve()` | summary only: max_stress, SF, yield, converged, iterations, peak-location line, `log_bytes` |
| `get_safety_factor()` | region forced to `all` |
| `set_parameter(name, value)` | constraint guard → `ConstraintViolation` (source=guard); **rejects if a different param is already overridden** → agent must `revert_parameters` first (keeps single-param invariant) |
| `revert_parameters()` | re-opens the part (clears overrides) |
| `save_part()` | path fixed `outputs/<part>.json`; guard: phase solved, no set since last solve, last SF ≥ 2.0, exactly 1 param ≠ base, constraints OK |
| `report_result(status, baseline_sf, final_sf, parameter, old, new, tried[], reason)` | terminal; status ∈ {no_change_needed, fixed, no_fix_possible, failed}; harness cross-checks final_sf vs last observed solve |

Excluded: get-solve-log, compute-stress-from-handbook, reset, close-part, list-parts, list-features (add only if needed).
`--no-guard` flag on the server to produce real violating traces for the eval.

## System prompt (generic strategy, not answers)
Task text verbatim; workflow order; solve is truth; one parameter only, never swap parts; constraints listed from
constraints.json; **search method**: baseline → for each parameter, probe the most favorable value allowed
(constraint limit, or a sane bound if unconstrained) to test feasibility with one solve → refine toward the minimal
change with ≥2.0 using observed SF; mesh failure = infeasible candidate, move on; if `is_latest:false`, mention it;
if no parameter reaches 2.0, report `no_fix_possible` with what was tried. Don't save unless fixed.

## Trace (harness side = ground truth; LLM side = stream-json)
`trace_schema_version`, `run_id`, `ts`, `duration_ms` on every event.
- `run_start{part, backend, model, system_prompt (full), constraints, budgets, tool_list, guard_enabled}`
- `tool_call{seq, name, args}` → `tool_result{seq, source: guard|cli|harness, ok, error_type, exit_code, shaped, shaped_bytes, raw_sha, raw_path(sidecar), phase_after, overrides_after}`
- `guard_reject{seq, rule, detail}`
- `oracle{saved_exists, saved_sha, resolved_sf, matches_claim, constraints_ok, single_param_ok}` (from verify_output, run by harness after agent exits)
- `run_end{report, solves, tool_calls, usage}`

## Checks to trust results
1. `check_nonhappy.py` (no LLM): (a) flange thickness 6.0 → ConstraintViolation, SDK params unchanged;
   (b) solve before mesh → StateError; (c) bad usage → UsageError; (d) save_part with SF<2 → rejected;
   (e) second param while first overridden → rejected. Each prints "PROVED: …".
2. `verify_output.py` oracle: fresh workdir, apply saved overrides, mesh, default load, solve → SF ≥ 2.0, == saved, constraints, single param.

## Part 2 eval
- Adapters → canonical `Run{run_id, harness, part, steps[Step{seq, ts, actor, tool, args, outcome{ok,error_type,source}, observations{sf,max_stress,phase}}], report, oracle}`; observations extracted per-tool via a table. Adapter #1: our harness JSONL (+ stream-json for LLM text). Adapter #2: stub for raw Claude Code stream-json only.
- ~7 deterministic checks in `eval/config.yaml` with `severity` + named thresholds: workflow_order, default_load_used,
  forbidden_tools, context_budget (shaped bytes), constraint_compliance, single_param_and_verified (solve after last
  set, SF≥2 before save), claim_matches_observation (+oracle). Optional off-by-default LLM judge for no-fix reasoning.
- Scoring: any critical fail → run FAIL; else weighted score. Aggregate: per-check pass rate by part/model/prompt version.
- Report: `eval/report.md` + `report.json`, evidence = step seq numbers.
- Flawed traces: 3 mutations of a real trace, each must fail **only** its target check: (a) claimed SF ≠ last solve,
  (b) constraint-violating set followed by save, (c) get-solve-log call / oversized result. `eval/test_flawed.py` asserts.

## Self-correction & learning (user requirement)
**In-run loop:** every tool error carries `error_type` + actionable `hint` (e.g. StateError → "call mesh first";
ConstraintViolation → limit value; second-param override → "revert_parameters first"). `report_result` is
cross-checked by the harness (final_sf vs last observed solve, save state vs status); a mismatch is returned as a
tool error so the agent corrects itself before the run ends (counted in trace as `self_correction`).

**Cross-run memory:** `memory/lessons.jsonl` (append-only, in git → persistent, diffable, reviewable).
Lesson = `{id, scope: global|tool:<name>|part:<name>, kind: process|observation, text (≤200 chars), evidence_run_ids,
source: reflection|eval, created, uses, confirmations}`.
- *Read (context-bounded):* agent calls `recall_lessons(scope)` → top-k (k=5, ≤1.5 KB) ranked by confirmations;
  never dumps the whole file. Process lessons are generic; part observations are hints only — the agent must still
  verify by solve (no hard-coded answers). `--memory off` gives clean, reproducible baseline runs.
- *Write:* (1) agent's terminal `record_lessons([...])` reflection; (2) `eval/learn.py` turns failed eval checks into
  process lessons (e.g. "verify by solve before report"). Dedup by normalized text; repeated → `confirmations++`.
- *Prove it:* run A (memory off) vs run B (memory on) on the same part; eval shows fewer solves/errors or equal.

## Repo
git repo rooted here; `.gitignore`: `runs/`, `.partlab/`, `.venv/`, `*.zip`. Commit per slice.

## Execution order
0. Decisions (below) + `--help` discovery per command.
1. client + tools + constraints + check_nonhappy → run (no LLM).  ← first runnable slice
2. MCP server + trace + run.py → bracket_v3, flange_v1.
3. verify_output oracle. 4. eval + flawed traces. 5. RUN.md.

## Open decisions for the user
1. Backend: subscription `claude -p` (default) vs API key.
2. flange_v1 fillet_radius is unconstrained: allowed as a fix (flagged as relying on no stated limit) vs treat only stated params.
3. Allow `Bash(./partlab:*)` + venv `pip install mcp`.
