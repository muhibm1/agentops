Verdict: 5 findings (0 critical, 0 high, 1 medium, 4 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | src/agentops/sources/simulator.py:196-209, 281-295 | A final ledger line that is complete JSON but lacks its newline is replayed as an entry, and the next `append_ledger_line` then seals it with a marker, which replay rejects (confirmed with a hand-built file on this machine; the append-then-replay path itself not run on POSIX) | Crash after the entry's JSON bytes reach disk but before the newline byte (POSIX; Windows text mode translates the separator to `\r\n`, so the check passes there by accident). `resume` replays the entry, the next apply writes `entry1\n{"torn_seal": true}\nentry2`, and the following replay raises `LedgerError` "seal marker that does not follow a torn fragment": the thread can no longer resume or show history | simulator.py `append_ledger_line`: only write the seal separator when the final unterminated text is not valid JSON; when it is valid JSON, write a bare `"\n"` first. Add a test with an unterminated valid-JSON last line followed by an append and a replay |
| low | src/agentops/evals/commands.py:186 | `load_split` catches `(OSError, ValidationError)` but not `UnicodeDecodeError` (believed, not run) | `agentops eval --model scripted` (dev split, no checksum pre-check) with a non-UTF-8 `evals/held_out/manifest.json` -> `held_out_ids_of` raises `UnicodeDecodeError` -> Python traceback instead of exit 2 with a sentence | commands.py:186: catch `(OSError, UnicodeDecodeError, ValidationError)`; add a CLI test with a Latin-1 manifest |
| low | src/agentops/evals/dataset.py:310-318 | `write_dataset` writes the manifest before it hashes the files, so a read failure leaves a new manifest with the old checksums (believed, not run) | `agentops dataset --write --force` while one held-out scenario file is unreadable (permission, lock) -> `sha256_of` raises `PermissionError` after the manifest was overwritten -> the pinned split and manifest disagree; the command exits 2 and `--check` then reports a mismatch | dataset.py: compute all digests first (build `lines`), then write the manifest and checksums; or write both to temp files and `os.replace` |
| low | src/agentops/evals/run.py:154 | `RUNNERS: dict[SystemName, Callable[..., RunOutcome]]` erases the runner signature, so pyright cannot check the `config=` keyword at `measured_run` line 163 (confirmed by reading; pyright not run) | A later change renames `config` in one runner -> still type-checks -> every row of that system becomes a `failed` TypeError row | run.py: define a `Runner` Protocol with `__call__(self, scenario: Scenario, *, config: RunnableConfig) -> RunOutcome` and use it in place of `Callable[..., RunOutcome]` |
| low | src/agentops/evals/commands.py:87 | `os.environ.update(project_environment(tracing))` mutates the process environment for the rest of the process | A test (or any in-process caller) invokes `eval_command` with tracing on -> `LANGSMITH_PROJECT` stays set -> later `tracing_status()` calls in the same process see a project they did not set | commands.py:87: pass the project in the run config metadata or restore the variable in a `try/finally`; or use `monkeypatch`-style save and restore around `run_split` |

Findings outside scope: none
Not verified: ruff, pyright and the pytest suite were not run (review of the diff only); the POSIX append-then-replay sequence in finding 1 was built by hand, not produced by a real crash; no CRITICAL security items found in the diff (no shell, SQL, eval or secret handling added; scenario ids are pattern-checked before use in paths).

## Re-check

Read `git diff 78bc4a4..HEAD` for simulator.py, dataset.py, commands.py and run.py plus the new test names. Did not run ruff, pyright or pytest (not verified).

| # | Status | Evidence |
|---|---|---|
| 1 medium (valid-JSON last line, no newline) | closed (confirmed by reading) | simulator.py `repair_separator` decides from the last line's content: valid JSON gets only "
", non-JSON gets "
" plus the marker. Test `test_a_final_entry_that_is_complete_json_without_its_newline_gets_only_a_newline` added. |
| 2 low (UnicodeDecodeError in `load_split`) | closed (confirmed by reading) | commands.py:197 now catches `(OSError, UnicodeDecodeError, ValidationError)`; `test_a_manifest_that_is_not_utf8_exits_two_for_eval_and_dataset` added. |
| 3 low (manifest written before hashing) | closed (confirmed by reading) | dataset.py `checksum_lines` reads every file and hashes the manifest text first; `test_an_unreadable_scenario_file_fails_the_write_before_either_file_is_touched` added. |
| 4 low (Runner Protocol) | deferred as agreed | run.py `RUNNERS` still `Callable[..., RunOutcome]`. |
| 5 low (os.environ.update) | deferred as agreed | commands.py still calls `os.environ.update`. |

New findings in changed code: none. `check_dataset` narrowed `except ValueError` to `ValidationError`; pydantic v2 raises `ValidationError` for bad JSON, so the manifest path is still covered (believed, not run).
