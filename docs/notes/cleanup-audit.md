# Repository clutter audit (2026-10-26)

Audit only: nothing is deleted in this PR yet. The owner marks each row in the **OK?** column
(`x` = do it, empty = keep as is); step 2 implements exactly the marked rows in this PR.
Base: `origin/dev` at `22e901d`. Line numbers refer to that commit.

**Read first: DS1 is in progress and not merged.** Its branch (`devteam/DS1`) rewrites 17 of
the web test files below (Sidebar, AppShell, NewInspectionDialog, ProcessingSteps, tokens,
noLiterals, login/control-center/root-catalog e2e, …). Web test edits made now would conflict
with DS1 and could pin the v1 look again. Proposal: do the devteam, pipeline, backend and
docs rows now; do the web rows (section a3) after DS1 merges, re-checked against its version.

Rule used: a test stays when it would catch a real regression (auth/roles, append-only decision
log, data integrity, pipeline numbers, Kerege gates and anti-bypass, screenshots/e2e of real
screens). It goes when it pins wording (prompt text, backlog order/text, docs text, exact class
strings, copy per language already covered by `keys.test.ts`), guards nothing, or duplicates
another test.

## a) Tests

### a1. devteam/tests (227 test functions)

| OK? | File:line | Test | What it checks | Proposal | Why |
|---|---|---|---|---|---|
| | test_backlog_demo_chain.py:47 | the_file_ends_with_the_demo_chain_then_later_in_the_owners_order | backlog.yaml task order = hard-coded list | delete | backlog order snapshot; breaks on every reorder |
| | test_backlog_demo_chain.py:54 | demo_chain_dependencies_are_the_owners_… | real depends_on = hard-coded map; DS1 goal text | delete | backlog text snapshot |
| | test_backlog_demo_chain.py:67 | later_tasks_say_why_and_acc1_replaces_p7 | blocked_reason prefix, "Replaces P7", branch names | delete | historical backlog state |
| | test_backlog_demo_chain.py:92 | every_demo_chain_goal_has_done_when_checks_and_a_scope | "Done when", "scale", "STOPPED until Kubernetes" in goals | delete | goal wording |
| | test_backlog_demo_chain.py:104 | p4a_2a_run_records_its_pr_and_the_notes_survive_a_redump | P4a-2a-run pr `/pull/58`, done_at, notes text | delete | one-off historical state |
| | test_backlog_demo_chain.py:82 | every_chain_and_later_goal_stays_in_its_developers_folders | `outside_paths` over the CHAIN/LATER goals | delete with the file, or generalise into test_deps.py over all non-done tasks | tied to a hard-coded task list (unsure) |
| | test_backlog_demo_chain.py:113 | later_tasks_are_never_planned_unblocked_or_resumed | the `later` status is never planned, unblocked or resumed | **keep: move to test_deps.py**, then delete the file | only test of the `later` status |
| | test_gates.py:715 | the_judge_numbers_its_criteria | `"number" in R.TESTER_SYSTEM` | delete | wording; the verdict schema requires `number`, test_gates.py:707 tests `criterion_key` |
| | test_gates.py:795 | tester_sendbacks_name_the_exact_place_and_judge_one_part | 7 substrings of TESTER_SYSTEM / BRAIN_SYSTEM | delete | prompt wording |
| | test_gates.py:806 | fixtures_rule_is_in_the_dev_tester_and_brain_prompts | one sentence in 3 prompts (greps real.py source) | delete | prompt wording |
| | test_gates.py:896 | mykolaiv_stays_ungated_by_the_owners_decision | config.yaml: mykolaiv ungated + its `why` text | delete | config snapshot; regression logic covered by test_gates.py:392, :851 |
| | test_scope.py:330 | frontend_design_skill_is_pinned_licensed_and_visible_to_codex | SKILL.md header, LICENSE text, README sha, AGENTS.md text | delete, keep only line 335 (`.agents` symlink) | one-time setup text |
| | test_continuous.py:64 | two_blocked_tasks_in_a_row_pause_the_developer | stop reason "paused: 2 … in a row" | delete | duplicate of test_supervisor.py:690 (stricter) |
| | test_procs.py:140 | screenshots_start_a_fresh_server_and_stop_it | server started, shots taken, port closed | merge into test_screenshots.py:283 (move the `served` check, l.145) | same server-stop assertions |
| | test_supervisor.py:1083 | a_v8_reference_screen_in_criteria_is_read_only_not_outside | screens/*.png allowed; DESIGN.md outside | merge into test_supervisor.py:267 `test_outside_paths` | l.1090-1092 repeats l.274-276 |
| | test_screenshots.py:111 | config_shoots_dev_app_in_both_themes_at_the_default_viewports | config themes, shoot args | merge into test_screenshots.py:251 | same config block, same assert |

Keep the test, drop only the wording lines:

| OK? | File:line | Test | Lines to drop | What stays |
|---|---|---|---|---|
| | test_gates.py:904 | the_tester_compares_screenshots_with_the_v8_reference_screens | TESTER_SYSTEM and `design_rule` text asserts (end of test) | image order, labels, approved-rewrite block, verdict |
| | test_gates.py:700 | web_developer_is_told_the_tester_runs_e2e | 702 exact phrase → `!= ""` | pipeline developer gets no e2e rule |
| | test_scope.py:314 | briefs_say_what_each_developer_may_write_in_docs | 317-322 (`docs_rule` text) | `SC.owned` / `SC.folder` |
| | test_scope.py:342 | skills_are_protected_and_never_downloaded_by_agents | 345 (`"DESIGN.md" in design_rule`) | skills paths protected |
| | test_supervisor.py:989 | codex_developer_runs_resumes_and_logs_load | 1017 prompt phrase → `str(scratch) in stdin` | run, resume, load logging |

Must stay: `test_deps.py:166` (`real_backlog_dependencies_name_existing_tasks`): after the demo-chain
file goes it is the only `@pytest.mark.backlog` test; `test_graph.py:112` runs `pytest -m backlog`
and would fail with "no tests selected" (exit 5) without it. The other 208 functions guard
behaviour (budget, gates, anti-bypass, merge rules, pre-push hook, screenshots, watchdog,
supervisor, safety, dashboard) and stay.

### a2. pipeline/tests (218) and backend/tests (57)

No test reads README/ADR/docs text or greps source files.

| OK? | File:line | Test | What it checks | Proposal | Why |
|---|---|---|---|---|---|
| | pipeline/tests/test_findings_real.py:28 (whole file) | facade_polygon_gives_three_frames_with_outlines_on_the_wall | a literal polygon on the historical run `data/runs/notre-dame-p3e`; writes PNGs to `data/findings-check` | delete | one-off check ("found once"), skipped unless that run exists; logic covered by test_findings.py:152 and backend test_findings_api.py:69 |
| | pipeline/tests/test_dense_remote_cli.py:103 | fake_run_prints_patch_match_knobs | the COLMAP commands the fake provider prints for patch_match knobs | delete (unsure) | argv pinned exactly in test_dense_remote.py:261; this one reads stdout |
| | pipeline/tests/test_dense_modal.py:155 | error_at_start_starts_nothing_to_stop | start error → steps start, stop_tagged | merge into :218 `other_start_errors_do_not_fall_back` | same fault, same assertion |
| | pipeline/tests/test_dense_remote.py:326 | repo_config_loads_disabled | repo config: dense off, budget ≥ 0 | merge into test_dense_modal.py:434 (move the ledger-under-`data/` assert, l.330) | duplicate (third copy in test_dense_remote_cli.py:94) |
| | pipeline/tests/test_dense_remote.py:120 | nonzero_exit_names_command_and_worker | exit 137 → message names command and worker | merge into :111 (`run_nonzero_exit` case, add `match=`) | same path |
| | pipeline/tests/test_dense_modal.py:285 | module_does_not_import_modal | subprocess import does not load `modal` | merge into test_dense_remote.py:414 (one subprocess, both modules) (unsure) | same pattern |
| | pipeline/tests/test_frames.py:382, :394, :402, helper :372 | blur / too_fast / pure_rotation | exact English advice text ("Hold the camera steadier", "slower", "turning on the spot", ends with ".") | keep tests, drop those lines | wording; numbers and `advice_key` stay |
| | pipeline/tests/test_frames.py:423 | frames_stage_writes_report | l.437-438 repeat the FIXED-mode rerun of :47-48 | drop the 2 lines | duplicate |
| | backend/tests/test_workflow_api.py:306 | subprocess_runner_reads_the_marker_or_reports_the_exit | l.319-320 dead `if '--list': pass` in the fake script; l.346-347 settings defaults unrelated to the runner | drop those lines | dead code / off-topic |

Backend optional merges (all in must-keep areas; default **keep**): `test_findings_api.py:150 test_roles`
and `test_decisions_api.py:43 test_viewer_post_is_403` overlap `test_roles.py` rows;
`test_decisions_api.py:171` overlaps `test_migrations.py:164` (append-only trigger). Keeping both is cheap
insurance for the decision log.

### a3. web (466 `it`/`test` call sites: 440 Vitest, 26 Playwright ≈ 56 runs) — after DS1 merges

| OK? | File:line | Test | What it checks | Proposal | Why |
|---|---|---|---|---|---|
| | src/shell/Sidebar.test.tsx:135 | renders visible uploaded and failed status chips | only `className === 'inspection-status-chip …'` | delete | text already in AppShell.test:213 |
| | src/shell/Sidebar.test.tsx:152 | unfamiliar API status verbatim | `className` `… unknown` | delete | same as AppShell.test:257 |
| | src/shell/Sidebar.test.tsx:161 | lists the uploaded inspection from GET /api/inspections | href, chip class, site count | merge into AppShell.test:213 | same data path |
| | src/shell/ProcessingSteps.test.tsx:117 | renders the S1 step fixture | data-state + step names | delete | same fixture/asserts as AppShell.test:257 |
| | src/shell/ProcessingSteps.test.tsx:158 | literal uploaded waiting state | one exact English sentence | delete | covered by :148 and AppShell.test:77 |
| | src/shell/NewInspectionDialog.test.tsx:140 | shows the i18n message for %s (5 cases) | exact English copy per error code | delete | each code tested with behaviour at :106, :160, :174, :186, :200 |
| | src/shell/NewInspectionDialog.test.tsx:269 | file validation errors in Russian and Kazakh | hard-coded ru/kk strings | delete | wording; `keys.test.ts` covers translations |
| | src/shell/AppShell.test.tsx:189 | failed demo stages | demo copy + data-state | merge into :173 | same route |
| | src/viewer/PointInfoPanel.test.tsx:127 | localizes panel labels (3) | labels per language | delete | `keys.test.ts` |
| | src/viewer/ModeSwitch.test.tsx:8 | Orbit active by default… | Orbit pressed, Walk disabled | delete | ViewerPage.test:510 + a6-navigation e2e |
| | src/viewer/ModeSwitch.test.tsx:22 | ignores keys 2 and 3 | key handling | delete | ViewerPage.test:482 + a6 e2e |
| | src/pages/ViewerPage.test.tsx:231 | point index equal to count | boundary | merge into :218 `it.each` | one more case |
| | src/pages/ViewerPage.test.tsx:238 | localizes point tool and invalid-link notice (3) | labels per language | merge into :185 (keep the hint check l.248) | wording |
| | src/pages/ViewerPage.test.tsx:459 | F, Esc, F for Observer | hint + minimap toggle | delete | navigation e2e :77-106 + engine.test:445 |
| | src/pages/ViewerPage.test.tsx:599 | localizes axis controls (3) | gizmo labels per language | merge into :408 | wording |
| | src/pages/ViewerPage.test.tsx:676 | localizes colour toggle tooltip (3) | tooltip + aria | merge into :694 | wording |
| | src/lib/farFixture.test.ts:24, :38 | fixture generator parses | e2e fixture only | delete | bundle.test:217-286 + far-layer e2e |
| | e2e/viewer.spec.ts:133 | signed-out home asks for login | login inputs shown | delete (e2e) | login.spec:13 covers it 6× |
| | e2e/control-center.spec.ts:199 | home lists API inspections, not the demo | same data as unit | delete (e2e) | AppShell.test:213 |
| | e2e: control-center:15, catalog-compatibility:10, upload:8 | (theme loops) | no colour asserted | drop the dark-theme runs (−9 runs) | theme irrelevant there |
| | e2e/root-catalog.spec.ts:9 | root lists legacy rows (6 runs) | rows, chips, auth GET | keep one run (−5) | nothing depends on theme/language |
| | src (10 `it.each(['en','ru','kk'])` behaviour tests, e.g. ProcessingSteps:30/148, PointInfoPanel:82/97, ViewerPage:301/383/510/651, NewInspectionDialog:302) | | behaviour × 3 languages | reduce to `en` (−20 cases) | translations covered by `keys.test.ts` |

Exact class-string lines to drop inside kept tests: Sidebar.test l.53, 55, 83; AppShell.test l.237, 450;
tokens.test l.98 (duplicate of l.70); NewInspectionDialog.test l.134 (duplicate of l.133).

Keep and flag: `noLiterals.test.ts` reads CSS/TSX as text, but it is the design-system lint
(no literal colours), not a wording check. `playwright-config.test` enforces `reuseExistingServer: false`.

## b) docs/notes/

Checked with `git grep` for every file name (code, CLAUDE.md, ADRs, architecture, README) and
the task's merged PR.

| OK? | File | Lines | Task / PR | Referenced by | Proposal |
|---|---|---|---|---|---|
| | DEF1-api-plan.md | 61 | DEF1-api, #71 merged | nothing | delete |
| | REV1-api-plan.md | 48 | REV1-api, #76 merged | nothing | delete |
| | S1-plan.md | 61 | S1, #69 merged | nothing | delete |
| | U2-plan.md | 128 | U2, #70 merged | nothing | delete |
| | b1-plan.md | 40 | B1, #67 merged | nothing | delete |
| | d1-storage-plan.md | 47 | D1, #72 merged | nothing | delete |
| | d1-s3-listing.md | 98 | D1 evidence (a bucket listing) | only d1-storage-plan.md | delete (owner's call: it is a verification record) |
| | P4a-2a-run-plan.md | 32 | P4a-2a-run, #58 merged | nothing | delete |
| | ux-dense-plan.md | 37 | UX-dense, done | nothing | delete |
| | kerege-supervisor-plan.md | 31 | Kerege supervisor, merged (a218b1d) | nothing | delete |
| | m1-plan.md | 225 | M1, done | ADR 0003, architecture.md, web/scripts/synthetic-bundle.mjs (§3 = bundle contract) | keep (it is a live contract) |
| | m2-plan.md + m2/raketa-masks.jpg | 123 | M2, done | ADR 0009, pipeline interfaces.py, segmentation.py (§2-3 contracts); image cited by ADR 0009 | keep |
| | devteam-plan.md | 66 | Kerege v1 | devteam/README.md | keep |
| | progress.md, benchmark.md, benchmark-method.md, datasets.md | | | CLAUDE.md, code, config | keep |
| | dense-speed.md, quality-report.md, refresh-runs.md, p3e-refresh-report.md, roi-format.md | | | code / config / architecture.md | keep |

DS1 adds 3 more plans (`DS1-plan.md`, `DS1-part1-plan.md`, `ds1-part2-plan.md`): under the new
rule they go in the PR that finishes DS1, or right after it.

## c) Dead code and leftovers

Method: every `web/src` module checked for an importer, every Python module under `pipeline/`,
`backend/app/`, `devteam/devteam/` for a mention in other code/config; every path in
CLAUDE.md, architecture.md, devteam/README.md, config.yaml checked for existence.

| OK? | Item | Fact | Proposal |
|---|---|---|---|
| | Unused source modules | none found (all web modules imported; all Python modules referenced) | — |
| | Stale paths in CLAUDE.md / architecture / README / config | none (devteam/README paths are relative to `devteam/`; `web/dense.*` are bundle paths) | — |
| | `web/src/design/tokens.json` groups `light` / `dark` / `shared` (v1) | on dev, the app still renders them; `v8` is not wired. On `devteam/DS1` the file is pure v8 (`version 8`, themes light/dark/midnight) | nothing now: DS1 removes them. Re-check after merge |
| | ADR 0018 (design tokens JSON) | `Status: accepted`, describes v1 (system font stacks). DS1 plan step 6 promises a superseding ADR | after DS1: `Status: superseded by <new ADR>` |
| | docs/design/DESIGN.md l.20-26 | says `v8` is "not wired yet", bundle.css "landed, not imported" | stale after DS1. `docs/design/*` is owner-only: owner edits |
| | docs/decisions/U2-upload-contract-adapter.md | the only ADR without a number; nothing links to it | rename to `0026-upload-contract-adapter.md` |
| | pipeline/pyproject.toml l.39 | marker `ml` declared, no test uses it | delete the line |
| | pipeline/tests/test_e2e.py l.12 | sample-video path assumes `<repo>/.claude/worktrees/<name>`; resolves wrongly elsewhere (test is `slow`, deselected by default) | fix the path (repo root + `data/samples`), keep the test |
| | pipeline/tests/test_frames.py l.18 | module-level ffmpeg `skipif` also skips ~19 pure-Python tests | move those tests to their own file (no deletion) |

Not clutter, but found during the audit (product, for a task, not this PR):
`web/src/viewer/PointInfoPanel.tsx` shows model confidence as a number (`PointInfoPanel.test.tsx:79`
expects `'0.800'`). CLAUDE.md / design system v8: confidence is stored but **never shown as a number**.

## d) Counts and time

| Area | Before | Removed (delete + merge) if all rows marked | After | Other |
|---|---|---|---|---|
| devteam test functions | 227 | 14 (+1 file) | 213 | 5 tests trimmed |
| pipeline test functions | 218 | 6 (2 delete, 4 merge; 2 of them unsure) | 212 | 1 file deleted; wording lines trimmed |
| backend test functions | 57 | 0 | 57 | 3 optional merges, default keep |
| web Vitest call sites | 440 | 18 (+ ~20 language cases) | 422 | after DS1 |
| web Playwright runs | ≈ 56 | 2 tests + ≈ 14 loop runs | ≈ 40 | after DS1 |
| docs/notes files | 22 | 10 | 12 | +3 DS1 plans later |

Time (measured, devteam, `pytest --durations=0`, 262 passed in 775 s): the rows above save
**≈ 10 s** (test_continuous.py:64 8.3 s, test_procs.py:140 1.6 s, the rest < 0.5 s), about 1 %.
The devteam suite's time is in behaviour tests (`test_watchdog.py` rehearse test alone: 73 s),
not in clutter. Web e2e (not measured here): ≈ 16 of ≈ 56 Playwright runs, roughly a quarter
of the e2e step. pipeline/backend: the removed tests are unit tests, seconds at most.
The main gain is less noise for agents and fewer wording tests that block real changes, not speed.
