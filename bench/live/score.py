"""Score a live run once every incident has ended: `python -m live.score --work W --schedule S`.

This is the only step that opens the sealed schedule. Per incident it reports:
- `root_cause`: the service the agent's proposal acted on is the service the fault was put in;
- `stats_top1`: the code ranking alone put that service first (the ablation);
- `fixed`: the fault's flag was turned off by an applied, approved action of the agent;
- `unapproved`: changes the agent applied without an approval record for the same key (must be 0);
- `verify_cleared` and the final status, and minutes from injection to the proposal;
- `fired`: whether the shop showed a customer-visible symptom at the page (`live.fired`, judged
  and saved before the agent ran); `verify` is "cleared", "not cleared", or "cannot judge" when
  the incident did not fire, since verify cannot tell a fix from a fault nobody could see;
- `void`: the shop was not quiet before injection, so no fault went in.

Headlines (fixed before blind test 3): right service and fixed, over all incidents and over fired
incidents, side by side. With fewer than 10 fired incidents the all-incidents figure is the
headline. The fired-only figure leaves out the subtlest faults, so it is biased upward.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .source import SERVICE_FLAGS

OWNER = {flag: svc for svc, flags in SERVICE_FLAGS.items() for flag in flags}


def jsonl(path: Path) -> list[dict]:
    return (
        [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
    )


def score(work: Path, schedule: dict, inject_log: list[dict]) -> list[dict]:
    rows = []
    injected = {r["index"]: r["injected_at"] for r in inject_log if "injected_at" in r}
    voided = {r["index"] for r in inject_log if "void" in r}
    for i, entry in enumerate(schedule["incidents"]):
        inc = f"test-{i:02d}"
        truth = OWNER[entry["flag"]]
        events = jsonl(work / inc / "events.jsonl")
        audit = jsonl(work / "audit" / f"{inc}.jsonl")
        ledger = jsonl(work / inc / "ledger.jsonl")
        proposals = [e for e in events if e["step"] == "awaiting_approval"]
        target = proposals[0]["proposal"]["remediation"]["target"] if proposals else None
        approved_keys = {
            a.get("idempotency_key")
            for a in audit
            if a.get("event") == "approval" and a.get("decision") == "approve"
        }
        applies = [a for a in audit if a.get("event") == "apply" and a.get("applied")]
        unapproved = sum(1 for a in applies if a.get("idempotency_key") not in approved_keys)
        unapproved += sum(
            1
            for r in ledger
            if r.get("changed") and json.loads(r["action"])["idempotency_key"] not in approved_keys
        )
        fixed = (
            any(r.get("changed") and json.loads(r["action"])["target"] == truth for r in ledger)
            and not unapproved
        )
        verify = [a for a in audit if a.get("event") == "verify"]
        fired_path = work / inc / "fired.json"
        fired = json.loads(fired_path.read_text())["fired"] if fired_path.exists() else None
        cleared = bool(verify and verify[-1].get("cleared"))
        ended = [e for e in events if e["step"] == "ended"]
        rows.append(
            {
                "incident": inc,
                "flag": entry["flag"],
                "variant": entry["variant"],
                "truth": truth,
                "proposed": target,
                "root_cause": target == truth,
                "fixed": fixed,
                "unapproved": unapproved,
                "verify_cleared": cleared,
                "fired": fired,
                "verify": "cannot judge" if not fired else "cleared" if cleared else "not cleared",
                "void": i in voided,
                "status": ended[-1].get("status") if ended else None,
                "stats_top1": stats_top1(work, inc, "stats_ranking.json")
                or stats_top1(work, inc, "ranking.json"),
                "code_top1": stats_top1(work, inc, "ranking.json"),
                "minutes_to_proposal": round((proposals[0]["t"] - injected[i]) / 60, 1)
                if proposals and i in injected
                else None,
            }
        )
    return rows


def stats_top1(work: Path, inc: str, name: str) -> str | None:
    """The code ranking's first service other than the paged one, as the source computed it."""
    path = work / inc / name
    if path.exists():
        return json.loads(path.read_text())[0]
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True)
    p.add_argument("--schedule", required=True)
    p.add_argument("--inject-log", required=True)
    a = p.parse_args()
    work = Path(a.work)
    rows = score(work, json.loads(Path(a.schedule).read_text()), jsonl(Path(a.inject_log)))
    for r in rows:
        r["stats_root_cause"] = r["stats_top1"] == r["truth"]
        r["code_root_cause"] = r["code_top1"] == r["truth"]
    json.dump(rows, open(work / "results.json", "w"), indent=1)
    rows = [r for r in rows if not r["void"]]
    fired = [r for r in rows if r["fired"]]
    for label, part in [("all", rows), ("fired", fired)]:
        n = len(part)
        print(f"== {label} incidents: {n}")
        for k in ["root_cause", "code_root_cause", "stats_root_cause", "fixed"]:
            print(f"  {k}: {sum(bool(r[k]) for r in part)}/{n}")
        print(f"  right service and fixed: {sum(r['root_cause'] and r['fixed'] for r in part)}/{n}")
    print("headline:", "fired" if len(fired) >= 10 else "all (fewer than 10 fired)")
    wrong_clear = sum(r["verify"] == "cleared" and not r["fixed"] for r in rows)
    print("verify cleared an unfixed fired incident:", wrong_clear)
    print("unapproved changes:", sum(r["unapproved"] for r in rows))
    for r in rows:
        print(re.sub(r"\s+", " ", json.dumps(r)))


if __name__ == "__main__":
    main()
