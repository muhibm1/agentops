# OpenRCA protocol (fixed before any test data is downloaded)

Develop on Market cloudbed-1 only, freeze, score Bank + Telecom + Market cloudbed-2 once with
Microsoft's `evaluate.py`, unmodified. The agent is never given the candidate-component list.
Ablations: statistics only, model only (same digest without code scores or order), both.

**Exposure, recorded 2026-11-23.** While confirming the query format, the design reviewer printed
the first six rows of Bank `query.csv` with their answers. Those six Bank queries are scored and
reported but left out of the headline, which covers the remaining test cases.

**Comparability.** Published Market figures cover both cloudbeds (148 cases); our Market test is
cloudbed-2 only, because cloudbed-1 is development data.

## Development build (Market cloudbed-1 only)

**Pipeline.** `reduce.py` shrinks each day's traces and logs to per-minute, per-component parquet
tables (`/home/user/rca-data/openrca/reduced/Market/cloudbed-1/`; raw CSVs kept). `query.py`
reads only the `instruction` (date, 30-minute window, failure count, fields asked; UTC+8 fixed).
`evidence.py` scores every KPI of every component (all `cmdb_id`s, plus each pod's service and
each node) against the same day outside the window, with the frozen RCAEval `_robust`/`_signal`
statistics, in two passes (window start, then the detected onset), and adds dominance (window peak
against the largest excursion of any other half hour that day). `rank.py` matches reason
signatures, merges pods into their service when two or more carry the same reason, and answers
time = earliest sustained out-of-band minute + 0 s. `bench/rcaeval/` is imported, not changed.

**Scorer.** `evaluate.py` is Microsoft's `main/evaluate.py` from github.com/microsoft/OpenRCA
(branch `main`, fetched 2026-11-23, sha256 9a1c2351...d53f1), unmodified except that the
repository's `.gitattributes` stores it with LF line endings (upstream uses CRLF); its licence is
`LICENSE-OpenRCA` (MIT; the file itself carries no header). Excluded from ruff in
`bench/ruff.toml` so it is never reformatted.

**Reason vocabulary** (answer format): the 15 Market reasons in OpenRCA's
`rca/baseline/rca_agent/prompt/basic_prompt_Market.py`. The candidate-component list in that file
is not used by any code or request.

**Fitted on cloudbed-1 only:** `DOM_WEIGHT` 2, `HOT` 12, `EARLY_PENALTY` 6 (onset in the
first 2 minutes), `TIME_OFFSET_S` 0, `SPIKE_MAX_MINUTES` 2, `SPACE_DOMINANCE` 1,
`STRESSOR_DOMINANCE` 5. Reason priors were tried and left at zero.

**Dev result, statistics only, official scorer** (70 queries; "overlapping" = the window, or the
ten minutes before it, holds more than one recorded fault; 38 overlapping, 32 clean):
all strict 0.400 (partial 0.616), clean strict 0.562 (partial 0.667). Per field (all): time
38/55, component 37/54, reason 28/55.

**Arms.** Statistics only: `run.py stats`. Model step: `run.py requests` writes
`requests/<q>.md` (shortlist with code scores) and `requests-model-only/<q>.md` (same evidence,
no scores, alphabetical) plus `answer.schema.json`; `run.py answers` validates
`answers/<q>.json` and falls back to the statistics answer when invalid.
