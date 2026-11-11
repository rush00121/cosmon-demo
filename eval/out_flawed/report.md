# PartLab agent eval report

**6 runs** — PASS 0, WARN 2, FAIL 4. Any failed *critical* check fails a run; score = severity-weighted pass fraction (warn = half).

## Runs

| run | part | status | verdict | score | failed / issues |
|---|---|---|---|---|---|
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | FAIL | 0.651 | **default_load_used**, **claim_matches_observation**, **verified_after_change** |
| `flange_v1-20261111T115647-memoff` | flange_v1 | no_fix_possible | FAIL | 0.812 | **constraint_compliance**, infeasibility_evidence |
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | FAIL | 0.866 | **verified_after_change**, workflow_order |
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | FAIL | 0.884 | **claim_matches_observation** |
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | WARN | 0.965 | forbidden_tools |
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | WARN | 0.965 | context_budget |

## Check pass rates

| check | pass | warn | fail | n/a |
|---|---|---|---|---|
| completed | 6 | 0 | 0 | 0 |
| default_load_used | 5 | 0 | 1 | 0 |
| claim_matches_observation | 4 | 0 | 2 | 0 |
| verified_after_change | 3 | 0 | 2 | 1 |
| single_parameter_change | 5 | 0 | 0 | 1 |
| constraint_compliance | 5 | 0 | 1 | 0 |
| oracle_agrees | 6 | 0 | 0 | 0 |
| state_consistency | 6 | 0 | 0 | 0 |
| status_correct | 0 | 0 | 0 | 6 |
| workflow_order | 5 | 1 | 0 | 0 |
| forbidden_tools | 5 | 0 | 1 | 0 |
| context_budget | 5 | 0 | 1 | 0 |
| error_recovery | 6 | 0 | 0 | 0 |
| infeasibility_evidence | 0 | 0 | 1 | 5 |
| efficiency | 6 | 0 | 0 | 0 |

## Findings (what to look at)

- `bracket_v3-20261111T115623-memoff` **verified_after_change** (critical, fail): parameters changed after the verifying solve — steps [26, 28, 27.5]
- `bracket_v3-20261111T115623-memoff` **workflow_order** (major, warn): 1 out-of-phase call(s), recovered — steps [28]
- `bracket_v3-20261111T115623-memoff` **claim_matches_observation** (critical, fail): final_sf 2.5 != verifying solve 2.198 — steps [26]
- `flange_v1-20261111T115647-memoff` **constraint_compliance** (critical, fail): constraint-violating value reached the SDK [constraints from trace] — steps [12]
- `flange_v1-20261111T115647-memoff` **infeasibility_evidence** (major, fail): declared infeasible without solving ['thickness_mm'] at an allowed bound
- `bracket_v3-20261111T115623-memoff` **forbidden_tools** (major, fail): forbidden tool used — steps [27.5]
- `bracket_v3-20261111T115623-memoff` **context_budget** (major, fail): results over 2000 B; 8292 B read by model (20664 B raw) — steps [10]
- `bracket_v3-20261111T115623-memoff` **default_load_used** (critical, fail): non-default load applied (default {'face_id': 'face_mounting', 'force_N': 5000, 'direction': '-Z'}) — steps [8, 16, 24]
- `bracket_v3-20261111T115623-memoff` **claim_matches_observation** (critical, fail): first solve is not a baseline (original parameters + default load) — steps [10]
- `bracket_v3-20261111T115623-memoff` **verified_after_change** (critical, fail): verifying solve did not use the default load — steps [26, 28]

## Pass rate by group

- **part**: bracket_v3: 0/5, flange_v1: 0/1
- **harness**: partlab_harness: 0/6
- **prompt**: ad6eeaf2b54b: 0/6
