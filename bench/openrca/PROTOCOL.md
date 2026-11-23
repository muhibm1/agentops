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
signatures, reads pod network corruption from the host node's receive-error counter, replaces pods
with their service when two or more are hot with onsets within 60 s (any reason; the service
outranks its best pod), and answers time = the earliest sustained out-of-band minute among the
chosen component's strong KPIs (any kind) + 0 s. `bench/rcaeval/` is imported, not changed.

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

**Review round 1 (2026-11-23), statistics only, official scorer, strict all-70 / clean-32.**
Baseline 28/70 (0.400) / 18/32 (0.562).
1. Local baseline (120 min before the window) beside the day baseline, stronger score kept per
   series: 27/70 (0.386) / 17/32 with the day's dominance, 26/70 with its own; with 2 and 3 on, 29
   against 30. Rejected (lowers strict); `LOCAL_BASELINE_S = 0`, code kept.
2. Service outranks its pods when two or more are hot at the same onset (60 s; 0 to 120 s equal),
   any reason: 29/70 (0.414) / 18/32 (0.562). Kept.
3. Time = earliest onset among the chosen component's strong KPIs: 30/70 (0.429) / 18/32. Kept.
4. Host counters: `net-rx-error` (UDP receive errors) rising marks packet corruption on the
   strongest pod of that node: 30/70 / 18/32, no change (corruption was already found from
   error KPIs). Kept. Retransmissions without receive errors as packet loss: strict unchanged but
   reason 27 -> 26 of 55 (relabelled latency faults); dropped.
5. Model step: the request shows the code's final answer as the default; `run.py answers` keeps a
   changed answer only when it cites evidence ids of the request, one on each changed component;
   the "moved first" guidance is gone from `answerer_prompt.md`; log templates have ids masked.
   The model-only variant is unchanged (no code answer, scores or order).
Final per field (all 70): time 39/55, component 41/54, reason 27/55; partial 0.641. Work folder
`/home/user/rca-data/openrca/work-cb1-v2/` (the earlier `work-cb1/` keeps the first model step).

**Arms.** Statistics only: `run.py stats`. Model step: `run.py requests` writes
`requests/<q>.md` (shortlist with code scores) and `requests-model-only/<q>.md` (same evidence,
no scores, alphabetical) plus `answer.schema.json`; `run.py answers` validates
`answers/<q>.json` and falls back to the statistics answer when invalid.

## Adapters for the sealed systems (Bank, Telecom), built label-free

`systems.py` holds one adapter per system: the reason vocabulary (the `POSSIBLE ROOT CAUSE
REASONS` list of OpenRCA's `rca/baseline/rca_agent/prompt/basic_prompt_<System>.py`, fetched
2026-11-23; the candidate-component lists there are not used), the KPI-kind tables, the reason
signatures and the component naming. Bank and Telecom tables and signatures were written from
their KPI names and telemetry schemas only. `reduce.py` recognises each system's trace and wide
metric columns from the file header and converts millisecond timestamps; `run.py extract
--system Bank|Telecom` selects the adapter, which the later stages read back from the evidence.

**Sample, for crash-testing only** (`/home/user/rca-data/openrca/sample/<System>/`): one date's
metric files (Bank 2021_03_04, Telecom 2020_04_11), the first 200 MB of that date's trace file
(and, for Bank, of its log file), and `query.csv` filtered on download to `task_index` and
`instruction`. No `scoring_points` were written to disk and no `record.csv` was downloaded. The
pipeline ran end to end on the queries of those dates (Bank 16, Telecom 6) with
`--skip-missing-days`, and every prediction validated (right count, a component the telemetry
prints, a reason from the system's vocabulary, a well-formed time). Nothing was tuned on the
sample; the constants fitted on Market cloudbed-1 apply unchanged. Re-reducing cloudbed-1 from its
raw CSVs with the generalised code and re-running every stage gives byte-identical statistics
answers and requests to the Market-only code (strict 30/70).

## Freeze (2026-11-23)

Development on Market cloudbed-1 ended here (`results/dev-cb1/`). Strict accuracy with Microsoft's
scorer, 70 queries: statistics alone 0.429; statistics plus the model step (the code's answer is
the default, the model may change a field only by citing evidence ids from the request) 0.486. On
the 32 clean windows: 0.562 and 0.625. The model step changed 17 answers. `FREEZE.sha256` holds the
hash of every file in this directory; nothing in it changes before the test is scored.

Test plan, to keep within the week's usage: the statistics arm and the statistics-plus-model arm
are scored on all test queries. The model-only ablation is not run on the test.
