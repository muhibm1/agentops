---
id: failed-read-must-not-mean-healthy
trigger: "when a verify or health check reads data and decides a problem has cleared"
confidence: 0.5
domain: testing
source: 2026-10-01-increment-3-act-pause-persist-resume
scope: project
---

## Action
Treat a failed, empty or range-less read as "cannot tell" and escalate; never as healthy. Write the failing-read test before the happy path.

## Evidence
- ecc-silent-failure-hunter high, verify.py:97-148: failed metrics read still declared cleared (fixed e71b24b).
- ecc-silent-failure-hunter low, verify.py:105: is_healthy true for a metric with no range (fixed).
