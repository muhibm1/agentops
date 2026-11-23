"""Replay the live health analysis over past incidents (development only):
`python -m live.replay --work W [--truth results.json] [--recent 180]`."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from . import blame, edges, stats, telemetry


def analyse_at(opened: float, recent_s: int, baseline_s: int):
    split = opened - recent_s
    start = split - baseline_s
    ev = stats.evidence(telemetry.metrics_frame(start, opened), split)
    return blame.analyse(ev, edges.window(start, split), edges.window(split, opened), recent_s, baseline_s)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True)
    p.add_argument("--truth")
    p.add_argument("--recent", type=int, default=180)
    p.add_argument("--baseline", type=int, default=600)
    p.add_argument("--exclude", default="frontend")
    a = p.parse_args()
    truth = {r["incident"]: r["truth"] for r in json.loads(Path(a.truth).read_text())} if a.truth else {}
    hits = n = 0
    for meta in sorted(Path(a.work).glob("*/meta.json")):
        inc = meta.parent.name
        opened = datetime.fromisoformat(json.loads(meta.read_text())["opened_at"]).timestamp()
        hs = [h for h in analyse_at(opened, a.recent, a.baseline) if h.service != a.exclude]
        t = truth.get(inc)
        ok = t is not None and hs[0].service == t
        hits += ok
        n += t is not None
        print(inc, "truth", t, "top", [(h.service, round(h.score, 1), h.status) for h in hs[:3]], "OK" if ok else "")
        for h in hs[:3]:
            for r in h.reasons:
                print("    ", h.service, "|", r[:150])
    print(f"top1 {hits}/{n}")


if __name__ == "__main__":
    main()
