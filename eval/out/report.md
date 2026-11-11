# PartLab agent eval report

**2 runs** — PASS 2, WARN 0, FAIL 0. Any failed *critical* check fails a run; score = severity-weighted pass fraction (warn = half).

## Runs

| run | part | status | verdict | score | failed / issues |
|---|---|---|---|---|---|
| [`bracket_v3-20261111T123320-memoff`](runs/bracket_v3-20261111T123320-memoff.md) | bracket_v3 | fixed | PASS | 1.0 | — |
| [`flange_v1-20261111T123320-memoff`](runs/flange_v1-20261111T123320-memoff.md) | flange_v1 | no_fix_possible | PASS | 1.0 | — |

## Check pass rates

| check | pass | warn | fail | n/a |
|---|---|---|---|---|
| completed | 2 | 0 | 0 | 0 |
| default_load_used | 2 | 0 | 0 | 0 |
| claim_matches_observation | 2 | 0 | 0 | 0 |
| verified_after_change | 1 | 0 | 0 | 1 |
| single_parameter_change | 1 | 0 | 0 | 1 |
| constraint_compliance | 2 | 0 | 0 | 0 |
| oracle_agrees | 2 | 0 | 0 | 0 |
| state_consistency | 2 | 0 | 0 | 0 |
| status_correct | 2 | 0 | 0 | 0 |
| workflow_order | 2 | 0 | 0 | 0 |
| forbidden_tools | 2 | 0 | 0 | 0 |
| context_budget | 2 | 0 | 0 | 0 |
| error_recovery | 2 | 0 | 0 | 0 |
| infeasibility_evidence | 1 | 0 | 0 | 1 |
| efficiency | 2 | 0 | 0 | 0 |

## Findings (what to look at)

- none

## Pass rate by group

- **part**: bracket_v3: 1/1, flange_v1: 1/1
- **harness**: partlab_harness: 2/2
- **prompt**: 4fba54fe43e9: 2/2
