"""Run AgentOps' root-cause method on OpenRCA queries, in stages.

    python -m openrca.run extract  --data SYSTEM_DIR --reduced REDUCED_DIR --work WORK
    python -m openrca.run stats    --work WORK
    python -m openrca.run requests --work WORK
    (the model step answers each WORK/requests/<query>.md into WORK/answers/<query>.json)
    python -m openrca.run answers  --work WORK
    python -m openrca.run score    --data SYSTEM_DIR --work WORK [--pred stats.csv]

Run from `bench/` with the benchmark environment (`bench/rcaeval/requirements.txt`).

`extract`, `stats`, `requests` and `answers` read only `query.csv`'s `instruction` column and the
reduced telemetry (`reduce.py`). `score` is the only stage that opens the answer key: the
`scoring_points` column, scored by Microsoft's `evaluate.py` vendored unmodified, and
`record.csv`, used only to label a query "overlapping" when its window holds more faults than the
query says. A model answer that is not valid JSON, breaks the schema, has the wrong number of
objects or uses a component that emitted no telemetry falls back to the statistics answer for that
query, and the fallback is counted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pickle
import re
from pathlib import Path

import pandas as pd

from . import evaluate as ms
from .digest import ANSWER_SCHEMA, request_text
from .evidence import DayFrame, load_day, new_templates, score_window
from .query import Query, parse, to_local
from .rank import REASONS, Candidate, all_candidates, answer_time, choose, final_reason

BENCH_FILES = ["query.py", "reduce.py", "evidence.py", "rank.py", "digest.py", "run.py"]
KEYS = ("root cause occurrence datetime", "root cause component", "root cause reason")


def fingerprint() -> dict[str, str]:
    here = Path(__file__).parent
    return {f: hashlib.sha256((here / f).read_bytes()).hexdigest()[:16] for f in BENCH_FILES}


def read_queries(data: Path) -> list[tuple[str, Query]]:
    """Query ids are the row order of query.csv: `q00`, `q01`, ... Only `instruction` is read."""
    out = []
    with open(data / "query.csv", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            out.append((f"q{i:02d}", parse(row["task_index"], row["instruction"])))
    return out


def _day(cache: dict[str, DayFrame], reduced: Path, work: Path, date: str) -> DayFrame:
    if date not in cache:
        p = work / "days" / f"{date}.pkl"
        if p.exists():
            cache[date] = pickle.load(open(p, "rb"))
        else:
            cache[date] = load_day(reduced, date)
            p.parent.mkdir(parents=True, exist_ok=True)
            pickle.dump(cache[date], open(p, "wb"))
    return cache[date]


def evidence_for(day: DayFrame, q: Query) -> dict:
    """Pass 1 splits at the window start; pass 2 re-scores from each picked candidate's onset."""
    pass1 = score_window(day, q.start, q.end)
    cands1 = all_candidates(pass1, q.start)
    onsets = sorted({c.onset for c in choose(cands1, q.failures) if c.onset is not None})
    pass2: dict[int, list] = {}
    for o in onsets:
        if q.end - o >= 5 * 60:
            pass2[o] = score_window(day, q.start, q.end, after_from=o)
    return {
        "query": q,
        "pass1": pass1,
        "pass2": pass2,
        "new_logs": new_templates(day, q.start, q.end),
        "host": day.host,
        "components": sorted(set(day.meta["comp"])),
    }


def candidates(ev: dict) -> list[Candidate]:
    """Merge both passes: a candidate keeps its stronger value and the earlier onset."""
    best: dict[tuple[str, str], Candidate] = {}
    for scored in [ev["pass1"], *ev["pass2"].values()]:
        for c in all_candidates(scored, ev["query"].start):
            k = (c.comp, c.reason)
            if k not in best or c.value > best[k].value:
                if k in best and best[k].onset is not None:
                    c.onset = min(best[k].onset, c.onset or best[k].onset)
                best[k] = c
    out = list(best.values())
    out.sort(key=lambda c: (-c.value, c.comp, c.reason))
    return out


def stats_answer(ev: dict) -> list[dict[str, str]]:
    q: Query = ev["query"]
    cands = candidates(ev)
    picked = choose(cands, q.failures)
    out = []
    for c in picked:
        out.append(
            {
                KEYS[0]: to_local(answer_time(c, q.start)),
                KEYS[1]: c.comp,
                KEYS[2]: final_reason(c, cands),
            }
        )
    while len(out) < q.failures:  # never fewer objects than failures
        out.append({KEYS[0]: to_local(q.start), KEYS[1]: "", KEYS[2]: REASONS[0]})
    return out


def prediction_text(objs: list[dict[str, str]]) -> str:
    return json.dumps({str(i + 1): {k: o[k] for k in KEYS} for i, o in enumerate(objs)})


def cmd_extract(a) -> None:
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    cache: dict[str, DayFrame] = {}
    evs = {}
    for qid, q in read_queries(Path(a.data)):
        day = _day(cache, Path(a.reduced), work, q.date)
        evs[qid] = evidence_for(day, q)
        print(qid, q.date, q.failures, ",".join(q.fields), flush=True)
    pickle.dump(evs, open(work / "evidence.pkl", "wb"))


def cmd_stats(a) -> None:
    work = Path(a.work)
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    rows = [{"id": qid, "prediction": prediction_text(stats_answer(ev))} for qid, ev in evs.items()]
    pd.DataFrame(rows).to_csv(work / "stats.csv", index=False)
    print(f"{len(rows)} statistics answers -> {work / 'stats.csv'}")


def cmd_requests(a) -> None:
    work = Path(a.work)
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    for variant, folder in (("both", "requests"), ("model-only", "requests-model-only")):
        out = work / folder
        out.mkdir(parents=True, exist_ok=True)
        for qid, ev in evs.items():
            (out / f"{qid}.md").write_text(request_text(qid, ev, candidates(ev), variant))
    (work / "answers").mkdir(exist_ok=True)
    (work / "answer.schema.json").write_text(json.dumps(ANSWER_SCHEMA, indent=1))
    json.dump(
        {"fingerprint": fingerprint(), "queries": len(evs)},
        open(work / "freeze.json", "w"),
        indent=1,
    )
    print(f"{len(evs)} requests in {work / 'requests'} and {work / 'requests-model-only'}")


DT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def validate(obj, q: Query, components: set[str]) -> list[dict[str, str]] | None:
    """The model's answer if it fits the schema and the query, else None."""
    if not isinstance(obj, dict) or not isinstance(obj.get("failures"), list):
        return None
    fs = obj["failures"]
    if len(fs) != q.failures:
        return None
    out = []
    for f in fs:
        if not isinstance(f, dict):
            return None
        dt, comp, reason = f.get("datetime"), f.get("component"), f.get("reason")
        if not (isinstance(dt, str) and DT.match(dt)):
            return None
        if reason not in REASONS or not isinstance(comp, str):
            return None
        if comp not in components:
            return None
        out.append({KEYS[0]: dt, KEYS[1]: comp, KEYS[2]: reason})
    return out


def known_components(ev: dict) -> set[str]:
    """Every component name the telemetry prints, plus the services and nodes it implies."""
    from .evidence import service_of

    comps = set(ev["components"])
    comps |= {s for c in comps if (s := service_of(c))}
    comps |= set(ev["host"].values())
    return comps


def cmd_answers(a) -> None:
    work = Path(a.work)
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    rows, fallbacks = [], []
    for qid, ev in evs.items():
        path = work / a.answers / f"{qid}.json"
        objs = None
        if path.exists():
            try:
                objs = validate(json.loads(path.read_text()), ev["query"], known_components(ev))
            except (json.JSONDecodeError, UnicodeDecodeError):
                objs = None
        if objs is None:
            fallbacks.append(qid)
            objs = stats_answer(ev)
        rows.append({"id": qid, "prediction": prediction_text(objs)})
    pd.DataFrame(rows).to_csv(work / a.out, index=False)
    print(f"{len(rows)} answers -> {work / a.out}; fallbacks {len(fallbacks)}: {fallbacks}")


def overlap_flags(data: Path, queries: list[tuple[str, Query]]) -> dict[str, bool]:
    """A query is overlapping when its window holds a second fault, or a fault from the ten
    minutes before the window can still be spilling into it."""
    rec = pd.read_csv(data / "record.csv")
    out = {}
    for qid, q in queries:
        n = int(((rec["timestamp"] >= q.start - 600) & (rec["timestamp"] < q.end)).sum())
        out[qid] = n > 1
    return out


def cmd_score(a) -> None:
    data, work = Path(a.data), Path(a.work)
    queries = read_queries(data)
    qdf = pd.read_csv(data / "query.csv")
    pred = pd.read_csv(work / a.pred).set_index("id")["prediction"]
    over = overlap_flags(data, queries)
    rows = []
    for i, (qid, q) in enumerate(queries):
        points = qdf.loc[i, "scoring_points"]
        passed, failed, score = ms.evaluate(pred[qid], points)
        comps = re.findall(r"predicted root cause component is ([^\n]+)", points)
        reasons = re.findall(r"predicted root cause reason is ([^\n]+)", points)
        times = re.findall(r"within 1 minutes \(i\.e\., <=1min\) of ([^\n]+)", points)
        rows.append(
            {
                "id": qid,
                "task": q.task,
                "failures": q.failures,
                "overlapping": over[qid],
                "score": score,
                "strict": score == 1.0,
                "time_ok": sum(p in times for p in passed),
                "time_n": len(times),
                "comp_ok": sum(p in comps for p in passed),
                "comp_n": len(comps),
                "reason_ok": sum(p in reasons for p in passed),
                "reason_n": len(reasons),
                "prediction": pred[qid],
                "truth": points.strip().replace("\n", " | "),
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(work / f"score-{Path(a.pred).stem}.csv", index=False)
    clean = df[~df["overlapping"]]

    def line(d: pd.DataFrame, name: str) -> str:
        def acc(f: str) -> str:
            n = d[f + "_n"].sum()
            return f"{d[f + '_ok'].sum()}/{n}={d[f + '_ok'].sum() / max(n, 1):.3f}"

        return (
            f"{name:9s} n={len(d):2d} strict={d['strict'].mean():.3f} "
            f"partial={d['score'].mean():.3f} time {acc('time')} component {acc('comp')} "
            f"reason {acc('reason')}"
        )

    print(line(df, "all"))
    print(line(clean, "clean"))
    print(df.groupby("task")[["strict", "score"]].mean().round(3).to_string())


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--data", required=True)
    e.add_argument("--reduced", required=True)
    e.add_argument("--work", required=True)
    s = sub.add_parser("stats")
    s.add_argument("--work", required=True)
    r = sub.add_parser("requests")
    r.add_argument("--work", required=True)
    an = sub.add_parser("answers")
    an.add_argument("--work", required=True)
    an.add_argument("--answers", default="answers")
    an.add_argument("--out", default="final.csv")
    sc = sub.add_parser("score")
    sc.add_argument("--data", required=True)
    sc.add_argument("--work", required=True)
    sc.add_argument("--pred", default="stats.csv")
    a = p.parse_args()
    {
        "extract": cmd_extract,
        "stats": cmd_stats,
        "requests": cmd_requests,
        "answers": cmd_answers,
        "score": cmd_score,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
