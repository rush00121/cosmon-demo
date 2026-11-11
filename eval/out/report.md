# PartLab agent eval report

**12 runs** — PASS 11, WARN 1, FAIL 0. Any failed *critical* check fails a run; score = severity-weighted pass fraction (warn = half).

## Runs

| run | part | status | verdict | score | failed / issues |
|---|---|---|---|---|---|
| `bracket_v3-20261111T115551-memoff-stress-budget_1_solve` | bracket_v3 | failed | WARN | 0.924 | status_correct |
| `bracket_v1-20261111T115421-memoff-stress-bracket_v1` | bracket_v1 | fixed | PASS | 1.0 | — |
| `bracket_v3_thick-20261111T115421-memoff-stress-bracket_v3_thick` | bracket_v3_thick | no_change_needed | PASS | 1.0 | — |
| `bracket_v3-20261111T115457-memoff-stress-fault_mesh_fail` | bracket_v3 | fixed | PASS | 1.0 | — |
| `bracket_v3-20261111T115507-memoff-stress-fault_solve_timeout` | bracket_v3 | fixed | PASS | 1.0 | — |
| `bracket_v3-20261111T115623-memoff` | bracket_v3 | fixed | PASS | 1.0 | — |
| `flange_v1-20261111T115647-memoff` | flange_v1 | no_fix_possible | PASS | 1.0 | — |
| `housing_v1-20261111T115421-memoff-stress-housing_v1` | housing_v1 | no_change_needed | PASS | 1.0 | — |
| `synth7_0-20261111T115431-memoff-stress-synth7_0` | synth7_0 | no_fix_possible | PASS | 1.0 | — |
| `synth7_1-20261111T115432-memoff-stress-synth7_1` | synth7_1 | fixed | PASS | 1.0 | — |
| `synth7_2-20261111T115445-memoff-stress-synth7_2` | synth7_2 | fixed | PASS | 1.0 | — |
| `synth7_3-20261111T115452-memoff-stress-synth7_3` | synth7_3 | fixed | PASS | 1.0 | — |

## Check pass rates

| check | pass | warn | fail | n/a |
|---|---|---|---|---|
| completed | 12 | 0 | 0 | 0 |
| default_load_used | 12 | 0 | 0 | 0 |
| claim_matches_observation | 11 | 0 | 0 | 1 |
| verified_after_change | 7 | 0 | 0 | 5 |
| single_parameter_change | 7 | 0 | 0 | 5 |
| constraint_compliance | 12 | 0 | 0 | 0 |
| oracle_agrees | 12 | 0 | 0 | 0 |
| state_consistency | 12 | 0 | 0 | 0 |
| status_correct | 11 | 1 | 0 | 0 |
| workflow_order | 12 | 0 | 0 | 0 |
| forbidden_tools | 12 | 0 | 0 | 0 |
| context_budget | 12 | 0 | 0 | 0 |
| error_recovery | 12 | 0 | 0 | 0 |
| infeasibility_evidence | 2 | 0 | 0 | 10 |
| efficiency | 12 | 0 | 0 | 0 |

## Findings (what to look at)

- `bracket_v3-20261111T115551-memoff-stress-budget_1_solve` **status_correct** (critical, warn): reported failed (ground truth fixed) after environment errors — steps [18]

## Pass rate by group

- **part**: bracket_v1: 1/1, bracket_v3_thick: 1/1, bracket_v3: 3/4, flange_v1: 1/1, housing_v1: 1/1, synth7_0: 1/1, synth7_1: 1/1, synth7_2: 1/1, synth7_3: 1/1
- **harness**: partlab_harness: 11/12
- **prompt**: ad6eeaf2b54b: 11/12
