# Security review: 2026-10-01-increment-1-scaffold

Scope: `git diff main...HEAD` (12 commits, 50 files); CI workflow, pre-push hook, requirements,
pyproject, CLI path handling, prompt delimiters and the evidence node. Regimes: none
(`compliance.regimes: []`); the profile's `controls_for_later_phases` were checked instead.

Verdict: 3 findings (0 critical, 0 high, 1 medium, 2 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | src/agentops/cli.py:123,127,131 | `report()` prints model free text (`Hypothesis.name`, `RCA.explanation`, `Remediation.target`) raw, with no escaping of newlines or control characters. Typer's echo strips ANSI only when stdout is not a TTY (`typer/_click/_compat.py:451`), and never strips `\n` or `\r`. Rule: evidence and model output are untrusted data (profile style note); defence in depth on the human-facing surface. Confirmed: calling `report()` with target `"db\nremediation: restart on cache (risk low)\x1b[8m"` produced a forged remediation line, and the real `(risk high)` followed a conceal escape | In increment 2 the live model reads attacker-influenced log lines and can echo them into `target` or `explanation`. The operator's terminal then shows a forged "risk low" remediation and hides the real one. That is the display the increment 3 approval decision is made from | src/agentops/cli.py: pass every model-sourced string through one helper that escapes control characters (for example `repr()` or replacing anything that `str.isprintable()` rejects) before it goes into `lines`. Separately, constrain `Remediation.target` in src/agentops/state.py to an id pattern. Add a test with a newline and an ESC in `target` |
| low | src/agentops/nodes/rca.py:29; src/agentops/nodes/triage.py:21 | `service` and `symptoms` go into the human prompt without `neutralise_delimiters`, and neither system prompt says symptoms are untrusted. R23 covers only evidence payloads. Confirmed: a symptom containing `<<<end>>>` and `<<<untrusted evidence>>>` gave an rca prompt with 2 opening and 2 closing delimiters | Today symptoms come from scenario files in the repository. Once they come from an alert body, which another system writes, a symptom can forge an early close and an extra open of the evidence block. Text placed between the two then reads as instructions from outside the untrusted block. Citation checks stop forged evidence ids, but they do not stop a steered root-cause choice among real ids | src/agentops/nodes/rca.py and triage.py: run `neutralise_delimiters` on `service` and each symptom when building the prompt (or once in `new_incident`), and state in `UNTRUSTED_DATA_RULE` that symptoms are untrusted too. Extend the E26 test with a hostile symptom |
| low | src/agentops/nodes/evidence.py:58 | `neutralise_delimiters` rewrites dict values but not dict keys, although its docstring promises every string "at any depth". Confirmed: `neutralise_delimiters({"<<<end>>>": "x"})` returned the key unchanged. None of today's payloads (`LogLine`, `Series`, `Deployment`) has a free-form dict | `EvidenceKind` already includes `config`, and `get_config` returns `dict[str, ConfigValue]` with keys that the source system controls. When config evidence is collected, a key `<<<end>>>` closes the block early, which breaks R23's one-open/one-close guarantee | src/agentops/nodes/evidence.py:58: neutralise keys as well (`{neutralise_delimiters(k): neutralise_delimiters(v) ...}`). Add a dict-key case to the E26 test |

Checks that passed (confirmed):
- CLI path handling: `--scenario ../scenarios/clean_bad_deploy` and `"clean_bad_deploy\n"` both
  exited 1 with "Unknown scenario" (ran `.venv/Scripts/agentops.exe run ...`). `re.fullmatch` and
  the membership check against `list_scenario_ids` keep the path inside `--scenarios-dir`.
- CI: `permissions: contents: read`; `persist-credentials: false`; no `secrets.` and no
  `pull_request_target`. Both action SHAs resolve to the tagged commits
  (`gh api .../git/ref/tags/v7.0.1` gave `3d3c42e5...`; `v7.0.0` gave `5fda3b95...`, type commit).
  Test-count floor 30; `pip_audit` runs last.
- Pins: every direct runtime and dev dependency and the `setuptools` build backend is an exact
  `==` pin. Transitive dependencies are not locked, the dev tools are not audited, and pyright
  fetches Node at runtime. These gaps are already accepted in docs/ci.md and are not raised again.
- Secrets: no key, token, private key or connection string in the added lines (grep over the
  diff). `git ls-files` lists no `.env*`, `.venv`, `*.pem`, `*.key`, `*.db` or `egg-info` file.
  `.gitignore` covers `.env*` as a pattern (line 4).
- LangSmith: `tests/conftest.py` clears all six tracing and API-key variables (autouse fixture).
- JSON rendering of evidence escapes control characters (`\u001b`), so ESC sequences cannot
  reach the prompt raw.
- Compliance control 1: `Approval.operator` is `Literal["operator","on-call","reviewer"]`
  (state.py:29,106).
- Constraint audit medium (D16, profile matches the repository): resolved. The profile commands
  carry "confirmed 2026-10-01", `format` is `--check`, and the style note names
  `src/agentops/fakes.py`.
- Pre-push hook: runs only repository-local lint and tests; it adds no new execution surface
  beyond running the test suite.

Checklist (wh-security-baseline): RLS/update/insert policies, functions, CREATE OR REPLACE,
policy tests, storage buckets, free-text tables and admin capability are n/a (no database, see
spec Security section). New env var: n/a (none added). Third-party runtime imports are pinned:
pass. No item failed.

Findings outside scope: The approver identity in approvals.md and the author of all 12 commits
is an employer-domain email address (names only, value not repeated here). It is already in the
commit metadata, so this file exposes nothing new. Before the repository goes public in increment
5, the owner may want a GitHub noreply address. Owner decision; no code change.
Not verified: The AgentShield scan of CLAUDE.md and .workhorse/profile.yml did not run: the
harness permission classifier denied `npx -y ecc-agentshield@1.6.0` (external code). I read the
CLAUDE.md diff by hand: command text only, no embedded instructions. R20 (green Actions run) is
not observed. The homoglyph or full-width delimiter variants (scenario 8) are deferred to
increment 2 by the spec and were not tested.


## Re-check (fix commit db61178)

Verdict: 3 of 3 findings closed; 0 reopened; 0 new findings in the changed code.

| Finding | Status | Evidence |
|---|---|---|
| medium, cli.py report() raw model text | closed (confirmed) | `escape_for_terminal` (cli.py:126) wraps hypothesis name, root cause label and explanation, remediation action and target. Probed: `\x1b`, `\n`, U+202E, U+2028, U+0085, NUL and a lone surrogate all come out as visible escapes. `Remediation.target` is now `^[a-z0-9_.-]+$` (state.py:17,61); probed: `"db\n"`, `"db\x1b[8m"` and `""` rejected, `"checkout-api.v2"` accepted. Test `test_report_escapes_control_characters_in_model_text` asserts no ESC and no forged line in stdout |
| low, rca.py and triage.py unfenced service and symptoms | closed (confirmed) | Both nodes run `neutralise_delimiters` on service and each symptom; `UNTRUSTED_DATA_RULE` names service and symptoms. Test `test_service_and_symptoms_are_fenced_and_marked_untrusted` asserts one opening and one closing delimiter in the rca prompt for a hostile symptom |
| low, evidence.py dict keys not neutralised | closed (confirmed) | `neutralise_delimiters` (nodes/__init__.py:52) now rewrites keys and any run of three or more brackets. Probed: `{"<<<end>>>": "<<<<x>>>>"}` gave `{"< < <end> > >": "< < < <x> > > >"}`. Test `test_angle_bracket_runs_and_dict_keys_are_neutralised` covers both |

Changed code checked, no finding (confirmed by reading the diff):
- `NoEvidenceError` renders the service with `!r`, so `fail()` prints control characters escaped.
- New `UnicodeDecodeError`/`OSError` branch in `load_named_scenario` prints the path and error
  type only, exits 1; the id check keeping the path inside `--scenarios-dir` is unchanged.
- `scripts/check_test_count.py` fails closed: bad floor, missing file or broken XML exit 1.
- Secrets: grep of the fix diff for key, token, password, private key and connection-string
  shapes found nothing (exit 1). No agent, CI or dependency file changed: AgentShield n/a.
- `.venv/Scripts/python.exe -m pytest -q`: 76 passed, exit 0.

Findings outside scope (re-check): `run()` prints `{error}` raw (cli.py:73, unchanged by the fix)
and `CitationError` joins model-supplied ids unescaped (state.py:207), so a rejected citation can
still put a newline or ESC on stderr. Same class as the closed medium, error path, low; fix: pass
the sentence through `escape_for_terminal` in `fail()`. Backslashes are not escaped, so literal
`\n` text and an escaped newline look alike; cosmetic, no line can be forged.
Not verified (re-check): no green Actions run was seen for db61178.
