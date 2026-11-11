# PartLab — Agent Interview

Welcome. You have **one hour** total, split into two parts.

You'll be working with **PartLab**, a small fake CAD/CAE SDK exposed as a CLI.
Think of it as a stand-in for SolidWorks + ANSYS: it has parametric parts, you
mesh them, apply loads, run a "solve", and read out stress / safety factor.

We do **not** care which agent harness you use.

---

## Setup (≤ 30 seconds)

There's nothing to install. The `partlab` binary in this directory is a
self-contained CLI. Run it from this directory so it can find `parts/`.

**macOS / Linux:**

```bash
./partlab --help
./partlab list-parts
./partlab open-part bracket_v3
./partlab mesh
./partlab apply-load face_mounting 5000
./partlab solve
./partlab get-safety-factor
```

**Windows (PowerShell or cmd):**

```
partlab.exe --help
partlab.exe list-parts
```

If those return JSON with `"ok": true`, you're set. Use `./partlab reset`
(or `partlab.exe reset` on Windows) between runs to clear the active session.

> Examples below use `./partlab` for brevity. On Windows substitute
> `partlab.exe`.

---

## Part 1 — Build the agent (≈ 30 min)

Build an agent that solves the following task end-to-end. Run it on at least
two parts: `bracket_v3` and `flange_v1`. **Capture the agent's trace** for
each run — messages, tool calls, tool results — in whatever format your
harness produces. You'll be analyzing those traces in Part 2, so make sure
they're persisted somewhere you can read later.

`./partlab reset` between runs to clear the SDK session (think of the binary as a stateful application).

### The task

> For the named part, apply the part's default load, run the FEA workflow,
> and report the safety factor. If SF < 2.0, propose a single-parameter
> change that achieves SF ≥ 2.0, verify by re-running the analysis, and save
> the updated part to `outputs/<part_name>.json`. If no fix is possible
> under the stated constraints, report that clearly instead of forcing a
> change.

### Per-part manufacturing constraints

Some parts have manufacturing constraints the SDK does **not** enforce —
your agent has to respect them.

| Part | Constraint |
| --- | --- |
| `flange_v1` | `thickness_mm ≤ 5.0` (sheet stock max), `hole_diameter_mm ≥ 4.0` (M4 bolt min) |

### What we're looking for

- A clean agent loop with a sensible system prompt and tool surface.
- The agent uses the SDK correctly (preconditions, ordering, recovery from
  errors).
- The agent doesn't over-fetch or pull large blobs back into context when it
  doesn't need to.
- The implementation separates CLI interaction, decision logic, and trace
  capture enough that a failure can be located and a new part can be added
  without rewriting the workflow. Use the PartLab solve as the source of truth;
  don't hard-code answers for the named parts.
- Show one small, repeatable check of a non-happy-path behavior, such as a
  failed CLI command, an invalid state transition, or a proposed change that
  violates a manufacturing constraint. The `flange_v1` run may serve as this
  check. Explain what the check proved.
- You can explain your design choices.

You do **not** need to handle every failure mode. Get the happy path solid;
note the rest.

---

## Part 2 — Build the eval (≈ 30 min)

Now write an eval harness that takes the **agent traces** from Part 1 and
produces a structured report. We're looking for an eval design you'd trust to
run on hundreds of traces, not a one-off script.

### Input

The raw traces your agent harness emitted in Part 1. Format depends on your
harness — Anthropic message arrays, Claude Code stream-json, OpenAI Agents
SDK runs, your own dict format, etc. Part of the exercise is wrangling that
into something an eval can reason over.

### What we're looking for

- A trace schema that generalizes (you should be able to drop in a fourth
  test case — or swap to a different harness — without rewriting things).
- Heuristics that are principled enough to tune — not magic numbers buried in
  conditionals.
- The report is something an engineer could actually read and act on.
- You can defend your scoring choices.
- Include one deliberately flawed trace (real or hand-made) and show that
  your eval flags the specific problem. An edited copy of one captured trace
  is fine. A short deterministic assertion or reproducible command is enough;
  a test framework is optional.

## Engineering process and handoff

Keep the one-hour time-box. We want to see how you work as well as the final
result. In the recording, briefly state your plan and assumptions, build a
small runnable slice, inspect its output, and narrate how you diagnose and
correct any issue you encounter. Show the checks you use to trust the agent's
reported outcomes, any saved part, and the eval report. AI coding tools are
welcome; verify their output rather than treating generated code as evidence.

Submit a short `RUN.md` with the commands needed to run the agent, its checks,
and the eval from a clean PartLab reset; list dependencies, the parts you ran,
what passed, and the main unfinished item or known limitation. Include source
code, tests or check scripts, the two or more raw traces, eval output, and any
saved `outputs/` parts. Keep this concise; no production packaging or CI is
expected.

---

### Workflow phases

```
closed --open_part--> open --mesh--> meshed --apply_load--> loaded --solve--> solved
```

`set_parameter` from any phase invalidates downstream state and drops you
back to `open`.

### Output format

Every command prints a JSON envelope to stdout:

```json
{ "ok": true,  "result": {...} }                                 # exit 0
{ "ok": false, "error": "...", "error_type": "StateError" }      # exit 1
```
