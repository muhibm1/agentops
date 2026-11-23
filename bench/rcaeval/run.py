"""Run AgentOps' root-cause method on RCAEval cases, in four stages.

    python -m rcaeval.run extract  --data DIR --cases 're2*,re3*' --out WORK
    python -m rcaeval.run digests  --work WORK [--anonymise] [--batch 8]
    (the model step answers each WORK/batches/batchN.txt into WORK/answers/outN.jsonl)
    python -m rcaeval.run score    --data DIR --work WORK

`extract` and `digests` never read a label. `score` is the only stage that opens the benchmark's
answer key (`cases.parquet`), and it runs after every answer is written. A model answer that names
a service outside the case, or is missing, falls back to the statistics ranking for that case, and
the fallback is counted.
"""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import pickle
import random
import re
from pathlib import Path

from .digest import case_id, digest
from .evidence import extract
from .rank import rank
from .score import ac_at, avg_at, labels
from .spans import add_logs, add_traces

BENCH_FILES = ["evidence.py", "rank.py", "spans.py", "digest.py", "prompt.md", "run.py", "score.py"]


def fingerprint() -> dict[str, str]:
    here = Path(__file__).parent
    return {f: hashlib.sha256((here / f).read_bytes()).hexdigest()[:16] for f in BENCH_FILES}


def cmd_extract(a) -> None:
    work = Path(a.out)
    work.mkdir(parents=True, exist_ok=True)
    data = Path(a.data)
    dirs = sorted(p for pat in a.cases.split(",") for p in glob.glob(str(data / pat)) if Path(p).is_dir())
    evs = {}
    for p in dirs:
        p = Path(p)
        ev = extract(p)
        add_traces(ev, p)
        add_logs(ev, p)
        evs[case_id(str(p))] = (p.name, ev)
    pickle.dump(evs, open(work / "evidence.pkl", "wb"))
    print(f"extracted {len(evs)} cases")


def _alias(cid: str, services: list[str]) -> dict[str, str]:
    rnd = random.Random(cid)
    used: set[str] = set()
    out = {}
    for s in services:
        while True:
            a = "svc-" + "".join(rnd.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(4))
            if a not in used:
                used.add(a)
                out[s] = a
                break
    return out


def anonymise(text: str, alias: dict[str, str]) -> str:
    for s in sorted(alias, key=len, reverse=True):
        text = re.sub(r"(?<![\w-])" + re.escape(s) + r"(?=_|,|\s|$|\))", alias[s], text)
    return text


def cmd_digests(a) -> None:
    work = Path(a.work)
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    out = work / "batches"
    out.mkdir(exist_ok=True)
    (work / "answers").mkdir(exist_ok=True)
    ids = sorted(evs)
    random.Random(0).shuffle(ids)  # mix systems and faults across batches
    aliases = {}
    for b in range(0, len(ids), a.batch):
        texts = []
        for cid in ids[b : b + a.batch]:
            _, ev = evs[cid]
            text, _, _ = digest(ev)
            if a.anonymise:
                al = _alias(cid, ev.services)
                aliases[cid] = {v: k for k, v in al.items()}
                text = anonymise(text, al)
            texts.append(text)
        (out / f"batch{b // a.batch}.txt").write_text("\n\n=====\n\n".join(texts))
    json.dump(aliases, open(work / "aliases.json", "w"))
    json.dump({"fingerprint": fingerprint(), "anonymised": a.anonymise, "cases": len(ids)}, open(work / "freeze.json", "w"), indent=1)
    print(f"{len(ids)} cases in {len(range(0, len(ids), a.batch))} batches")


def cmd_score(a) -> None:
    work = Path(a.work)
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    aliases = json.load(open(work / "aliases.json"))
    answers: dict[str, dict] = {}
    for f in sorted(glob.glob(str(work / "answers" / "*.jsonl"))):
        for line in open(f):
            line = line.strip()
            if line:
                try:
                    o = json.loads(line)
                    answers[o["case"]] = o
                except (json.JSONDecodeError, KeyError):
                    continue
    lab = labels(Path(a.data))
    rows = []
    fallbacks = 0
    for cid, (name, ev) in evs.items():
        stats = rank(ev)
        metric_only = copy.copy(ev)
        metric_only.signals = [g for g in ev.signals if not re.search(r"_(trace|log)-", g.column)]
        metrics = rank(metric_only)
        o = answers.get(cid)
        model = None
        if o and isinstance(o.get("ranking"), list):
            back = aliases.get(cid, {})
            r = [back.get(x, x) for x in o["ranking"]]
            if r and all(x in ev.services for x in r):
                model = r + [s for s in stats if s not in r]
        if model is None:
            fallbacks += 1
            model = stats
        t = lab[name]
        rows.append(
            {
                "case": name, "dataset": t["dataset"], "fault": t["fault"], "truth": t["service"],
                "metrics_AC@1": ac_at(metrics, t["service"], 1), "metrics_Avg@5": avg_at(metrics, t["service"]),
                "stats_top": stats[:5], "model_top": model[:5],
                "stats_AC@1": ac_at(stats, t["service"], 1), "stats_AC@3": ac_at(stats, t["service"], 3), "stats_Avg@5": avg_at(stats, t["service"]),
                "model_AC@1": ac_at(model, t["service"], 1), "model_AC@3": ac_at(model, t["service"], 3), "model_Avg@5": avg_at(model, t["service"]),
                "fault_type_said": (o or {}).get("fault_type"),
            }
        )
    json.dump({"rows": rows, "fallbacks": fallbacks, "freeze": json.load(open(work / "freeze.json"))}, open(work / "results.json", "w"), indent=1)
    import pandas as pd

    df = pd.DataFrame(rows)
    cols = ["metrics_AC@1", "stats_AC@1", "model_AC@1", "stats_AC@3", "model_AC@3", "stats_Avg@5", "model_Avg@5"]
    t = df.groupby("dataset")[cols].mean().round(3)
    t["n"] = df.groupby("dataset").size()
    print(t.to_string())
    print("all", df[cols].mean().round(3).to_dict(), "fallbacks", fallbacks)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--data", required=True)
    e.add_argument("--cases", required=True)
    e.add_argument("--out", required=True)
    d = sub.add_parser("digests")
    d.add_argument("--work", required=True)
    d.add_argument("--anonymise", action="store_true")
    d.add_argument("--batch", type=int, default=8)
    s = sub.add_parser("score")
    s.add_argument("--data", required=True)
    s.add_argument("--work", required=True)
    a = p.parse_args()
    {"extract": cmd_extract, "digests": cmd_digests, "score": cmd_score}[a.cmd](a)


if __name__ == "__main__":
    main()
