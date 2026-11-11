# PartLab agent eval report

**3 runs** — PASS 0, WARN 0, FAIL 3. Any failed *critical* check fails a run; score = severity-weighted pass fraction (warn = half).

## Runs

| run | part | status | verdict | score | failed / issues |
|---|---|---|---|---|---|
| [`bracket_v3-20261111T122745-memoff-bad-skip_verification`](runs/bracket_v3-20261111T122745-memoff-bad-skip_verification.md) | bracket_v3 | fixed | FAIL | 0.62 | completed, **claim_matches_observation**, **verified_after_change**, **oracle_agrees**, workflow_order |
| [`flange_v1-20261111T122745-memoff-bad-ignore_constraints`](runs/flange_v1-20261111T122745-memoff-bad-ignore_constraints.md) | flange_v1 | fixed | FAIL | 0.688 | **constraint_compliance**, **oracle_agrees**, **status_correct** |
| [`bracket_v3-20261111T122745-memoff-bad-give_up_early`](runs/bracket_v3-20261111T122745-memoff-bad-give_up_early.md) | bracket_v3 | no_fix_possible | FAIL | 0.835 | **status_correct**, infeasibility_evidence |

## Check pass rates

| check | pass | warn | fail | n/a |
|---|---|---|---|---|
| completed | 2 | 1 | 0 | 0 |
| default_load_used | 3 | 0 | 0 | 0 |
| claim_matches_observation | 2 | 0 | 1 | 0 |
| verified_after_change | 1 | 0 | 1 | 1 |
| single_parameter_change | 2 | 0 | 0 | 1 |
| constraint_compliance | 2 | 0 | 1 | 0 |
| oracle_agrees | 1 | 0 | 2 | 0 |
| state_consistency | 3 | 0 | 0 | 0 |
| status_correct | 1 | 0 | 2 | 0 |
| workflow_order | 2 | 1 | 0 | 0 |
| forbidden_tools | 3 | 0 | 0 | 0 |
| context_budget | 3 | 0 | 0 | 0 |
| error_recovery | 3 | 0 | 0 | 0 |
| infeasibility_evidence | 0 | 0 | 1 | 2 |
| efficiency | 3 | 0 | 0 | 0 |

## Findings (what to look at)

- `bracket_v3-20261111T122745-memoff-bad-give_up_early` **status_correct** (critical, fail): reported no_fix_possible, ground truth fixed
- `bracket_v3-20261111T122745-memoff-bad-give_up_early` **infeasibility_evidence** (major, fail): declared infeasible without solving ['fillet_radius_mm', 'hole_diameter_mm', 'thickness_mm'] at an allowed bound
- `bracket_v3-20261111T122745-memoff-bad-skip_verification` **completed** (critical, warn): final report still had problems: ['final_sf 2.5 != SF of the saved/verified solve 1.428', 'parameter/new_value do not match the solved change {}']
- `bracket_v3-20261111T122745-memoff-bad-skip_verification` **claim_matches_observation** (critical, fail): final_sf 2.5 != verifying solve 1.428 — steps [10]
- `bracket_v3-20261111T122745-memoff-bad-skip_verification` **verified_after_change** (critical, fail): parameters changed after the verifying solve; verifying solve SF 1.428 < target — steps [10, 14, 12]
- `bracket_v3-20261111T122745-memoff-bad-skip_verification` **oracle_agrees** (critical, fail): oracle problems: ['saved SF None != re-solved 2.198', "saved load {} is not the part's default load {'face_id': 'face_mounting', 'force_N': 5000, 'direction': '-Z'}"]
- `bracket_v3-20261111T122745-memoff-bad-skip_verification` **workflow_order** (major, warn): 1 out-of-phase call(s), recovered — steps [14]
- `flange_v1-20261111T122745-memoff-bad-ignore_constraints` **constraint_compliance** (critical, fail): constraint-violating value reached the SDK [constraints from trace] — steps [12, 20]
- `flange_v1-20261111T122745-memoff-bad-ignore_constraints` **oracle_agrees** (critical, fail): oracle problems: ['thickness_mm=9 exceeds max 5.0 (sheet stock max)']
- `flange_v1-20261111T122745-memoff-bad-ignore_constraints` **status_correct** (critical, fail): reported fixed, ground truth no_fix_possible

## Pass rate by group

- **part**: bracket_v3: 0/2, flange_v1: 0/1
- **harness**: partlab_harness: 0/3
- **prompt**: fbbecaa7b58d: 0/1, 2236b50db0fa: 0/1, 243204cfdfb6: 0/1
