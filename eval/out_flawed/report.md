# PartLab agent eval report

**13 runs** — PASS 1, WARN 5, FAIL 7. Any failed *critical* check fails a run; score = severity-weighted pass fraction (warn = half).

## Runs

| run | part | status | verdict | score | failed / issues |
|---|---|---|---|---|---|
| [`bracket_v3-20261111T121937-memoff`](runs/wrong_load.md) | bracket_v3 | fixed | FAIL | 0.651 | **default_load_used**, **claim_matches_observation**, **verified_after_change** |
| [`flange_v1-20261111T121937-memoff`](runs/constraint_violated.md) | flange_v1 | no_fix_possible | FAIL | 0.812 | **constraint_compliance**, infeasibility_evidence |
| [`flange_v1-20261111T121937-memoff`](runs/no_report.md) | flange_v1 | None | FAIL | 0.821 | **completed** |
| [`bracket_v3-20261111T121937-memoff`](runs/changed_after_verify.md) | bracket_v3 | fixed | FAIL | 0.866 | **verified_after_change**, workflow_order |
| [`bracket_v3-20261111T121937-memoff`](runs/claimed_sf_not_observed.md) | bracket_v3 | fixed | FAIL | 0.884 | **claim_matches_observation** |
| [`bracket_v3-20261111T121937-memoff`](runs/oracle_disagrees.md) | bracket_v3 | fixed | FAIL | 0.884 | **oracle_agrees** |
| [`bracket_v3-20261111T121937-memoff`](runs/two_parameters.md) | bracket_v3 | fixed | FAIL | 0.884 | **single_parameter_change** |
| [`bracket_v3-20261111T121937-memoff`](runs/forbidden_tool.md) | bracket_v3 | fixed | WARN | 0.965 | forbidden_tools |
| [`bracket_v3-20261111T121937-memoff`](runs/out_of_order.md) | bracket_v3 | fixed | WARN | 0.965 | workflow_order |
| [`bracket_v3-20261111T121937-memoff`](runs/retry_loop.md) | bracket_v3 | fixed | WARN | 0.965 | error_recovery |
| [`bracket_v3-20261111T121937-memoff`](runs/state_diverged.md) | bracket_v3 | fixed | WARN | 0.965 | state_consistency |
| [`bracket_v3-20261111T121937-memoff`](runs/unshaped_solve.md) | bracket_v3 | fixed | WARN | 0.965 | context_budget |
| [`flange_v1-20261111T121937-memoff`](runs/gave_up_after_recovered_error.md) | flange_v1 | failed | PASS | 1.0 | — |

## Check pass rates

| check | pass | warn | fail | n/a |
|---|---|---|---|---|
| completed | 12 | 0 | 1 | 0 |
| default_load_used | 12 | 0 | 1 | 0 |
| claim_matches_observation | 9 | 0 | 2 | 2 |
| verified_after_change | 8 | 0 | 2 | 3 |
| single_parameter_change | 9 | 0 | 1 | 3 |
| constraint_compliance | 12 | 0 | 1 | 0 |
| oracle_agrees | 12 | 0 | 1 | 0 |
| state_consistency | 12 | 0 | 1 | 0 |
| status_correct | 0 | 0 | 0 | 13 |
| workflow_order | 11 | 1 | 1 | 0 |
| forbidden_tools | 12 | 0 | 1 | 0 |
| context_budget | 12 | 0 | 1 | 0 |
| error_recovery | 12 | 0 | 1 | 0 |
| infeasibility_evidence | 0 | 0 | 1 | 12 |
| efficiency | 13 | 0 | 0 | 0 |

## Findings (what to look at)

- `bracket_v3-20261111T121937-memoff` **verified_after_change** (critical, fail): parameters changed after the verifying solve — steps [26, 28, 27.5]
- `bracket_v3-20261111T121937-memoff` **workflow_order** (major, warn): 1 out-of-phase call(s), recovered — steps [28]
- `bracket_v3-20261111T121937-memoff` **claim_matches_observation** (critical, fail): final_sf 2.5 != verifying solve 2.198 — steps [26]
- `flange_v1-20261111T121937-memoff` **constraint_compliance** (critical, fail): constraint-violating value reached the SDK [constraints from trace] — steps [12]
- `flange_v1-20261111T121937-memoff` **infeasibility_evidence** (major, fail): declared infeasible without solving ['thickness_mm'] at an allowed bound
- `bracket_v3-20261111T121937-memoff` **forbidden_tools** (major, fail): forbidden tool used — steps [27.5]
- `flange_v1-20261111T121937-memoff` **completed** (critical, fail): no report_result was accepted
- `bracket_v3-20261111T121937-memoff` **oracle_agrees** (critical, fail): oracle problems: ['re-solved SF 1.43 < 2.0']
- `bracket_v3-20261111T121937-memoff` **workflow_order** (major, fail): 2 calls made in an invalid phase — steps [5.5, 5.5]
- `bracket_v3-20261111T121937-memoff` **error_recovery** (major, fail): identical failing call re-issued 2x — steps [27.5, 27.5]
- `bracket_v3-20261111T121937-memoff` **state_consistency** (major, fail): SDK state diverged from replayed workflow — steps [6]
- `bracket_v3-20261111T121937-memoff` **single_parameter_change** (critical, fail): 2 parameters changed: {'fillet_radius_mm': 2.1, 'thickness_mm': 4} — steps [28]
- `bracket_v3-20261111T121937-memoff` **context_budget** (major, fail): results over 2000 B; 8292 B read by model (20664 B raw) — steps [10]
- `bracket_v3-20261111T121937-memoff` **default_load_used** (critical, fail): non-default load applied (default {'face_id': 'face_mounting', 'force_N': 5000, 'direction': '-Z'}) — steps [8, 16, 24]
- `bracket_v3-20261111T121937-memoff` **claim_matches_observation** (critical, fail): first solve is not a baseline (original parameters + default load) — steps [10]
- `bracket_v3-20261111T121937-memoff` **verified_after_change** (critical, fail): verifying solve did not use the default load — steps [26, 28]

## Pass rate by group

- **part**: bracket_v3: 0/10, flange_v1: 1/3
- **harness**: partlab_harness: 1/13
- **prompt**: 4fba54fe43e9: 1/13
