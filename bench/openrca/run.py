"""Run AgentOps' root-cause method on OpenRCA queries, in stages.

    python -m openrca.run extract  --data SYSTEM_DIR --reduced REDUCED_DIR --work WORK
                                   [--system Market|Bank|Telecom]
    python -m openrca.run stats    --work WORK
    python -m openrca.run requests --work WORK
    (the model step answers each WORK/requests/<query>.md into WORK/answers/<query>.json)
    python -m openrca.run answers  --work WORK [--answers DIR --variant both|model-only]
    python -m openrca.run score    --data SYSTEM_DIR --work WORK [--pred stats.csv]

Run from `bench/` with the benchmark environment (`bench/rcaeval/requirements.txt`).

`extract`, `stats`, `requests` and `answers` read only `query.csv`'s `instruction` column and the
reduced telemetry (`reduce.py`). `score` is the only stage that opens the answer key: the
`scoring_points` column, scored by Microsoft's `evaluate.py` vendored unmodified, and
`record.csv`, used only to label a query "overlapping" when its window holds more faults than the
query says. A model answer that is not valid JSON, breaks the schema, has the wrong number of
objects or uses a component that emitted no telemetry falls back to the statistics answer for that
query, and the fallback is counted. In the `both` variant the request shows the statistics answer
as the default, and a model answer that changes it must cite evidence ids from the request (one of
them on each changed component), or it falls back too.
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
from . import systems
from .digest import answer_schema, request_text
from .evidence import DayFrame, load_day, new_templates, score_window
from .query import Query, parse, to_local
from .rank import Candidate, all_candidates, answer_time, choose, final_reason

BENCH_FILES = [
    "query.py",
    "reduce.py",
    "systems.py",
    "evidence.py",
    "rank.py",
    "digest.py",
    "run.py",
]
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
        # Keyed by the loader's own hash, so a changed KPI table never reuses a stale cache.
        fp = fingerprint()
        key = hashlib.sha256((fp["evidence.py"] + fp["systems.py"]).encode()).hexdigest()[:16]
        p = work / "days" / f"{systems.current().name}-{date}-{key}.pkl"
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
    cands1 = all_candidates(pass1, q.start, day.host)
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
        "system": systems.current().name,
    }


def candidates(ev: dict) -> list[Candidate]:
    """Merge both passes: a candidate keeps its stronger value and the earlier onset."""
    best: dict[tuple[str, str], Candidate] = {}
    for scored in [ev["pass1"], *ev["pass2"].values()]:
        for c in all_candidates(scored, ev["query"].start, ev["host"]):
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
    scored = [s for sc in [ev["pass1"], *ev["pass2"].values()] for s in sc]
    out = []
    for c in picked:
        out.append(
            {
                KEYS[0]: to_local(answer_time(c, q.start, scored)),
                KEYS[1]: c.comp,
                KEYS[2]: final_reason(c, cands),
            }
        )
    while len(out) < q.failures:  # never fewer objects than failures
        out.append({KEYS[0]: to_local(q.start), KEYS[1]: "", KEYS[2]: systems.current().reasons[0]})
    return out


def prediction_text(objs: list[dict[str, str]]) -> str:
    return json.dumps({str(i + 1): {k: o[k] for k in KEYS} for i, o in enumerate(objs)})


def load_evidence(work: Path) -> dict:
    """The stored evidence, with its system made active."""
    evs = pickle.load(open(work / "evidence.pkl", "rb"))
    first = next(iter(evs.values()), {})
    systems.use(first.get("system", "Market"))
    return evs


def cmd_extract(a) -> None:
    systems.use(a.system)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    cache: dict[str, DayFrame] = {}
    evs = {}
    skipped = []
    for qid, q in read_queries(Path(a.data)):
        if a.skip_missing_days and not (Path(a.reduced) / q.date).is_dir():
            skipped.append(qid)
            continue
        day = _day(cache, Path(a.reduced), work, q.date)
        evs[qid] = evidence_for(day, q)
        print(qid, q.date, q.failures, ",".join(q.fields), flush=True)
    pickle.dump(evs, open(work / "evidence.pkl", "wb"))
    if skipped:
        print(f"{len(skipped)} queries skipped, no reduced telemetry for their date", flush=True)


def cmd_stats(a) -> None:
    work = Path(a.work)
    evs = load_evidence(work)
    rows = [{"id": qid, "prediction": prediction_text(stats_answer(ev))} for qid, ev in evs.items()]
    pd.DataFrame(rows).to_csv(work / "stats.csv", index=False)
    print(f"{len(rows)} statistics answers -> {work / 'stats.csv'}")


def cmd_requests(a) -> None:
    work = Path(a.work)
    evs = load_evidence(work)
    for variant, folder in (("both", "requests"), ("model-only", "requests-model-only")):
        out = work / folder
        out.mkdir(parents=True, exist_ok=True)
        for qid, ev in evs.items():
            text = request_text(qid, ev, candidates(ev), variant, stats_answer(ev))
            (out / f"{qid}.md").write_text(text)
    (work / "answers").mkdir(exist_ok=True)
    schema = answer_schema(systems.current().reasons)
    (work / "answer.schema.json").write_text(json.dumps(schema, indent=1))
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
        if reason not in systems.current().reasons or not isinstance(comp, str):
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


EVIDENCE_LINE = re.compile(r"^\s+\[(e\d+)\] (\S+) ", re.MULTILINE)


def cited_components(text: str, cites) -> set[str] | None:
    """The components named by the cited evidence lines of a request, or None when `cites` is
    not a list of ids that all appear in it."""
    lines = dict(EVIDENCE_LINE.findall(text))
    if not isinstance(cites, list) or not cites:
        return None
    if not all(isinstance(c, str) and c in lines for c in cites):
        return None
    return {lines[c] for c in cites}


def override_ok(obj: dict, objs, code, text: str, host: dict[str, str]) -> bool:
    """A model answer that differs from the code answer stands only when it cites evidence ids
    from its request, and, for every changed component, at least one cited line belongs to that
    component (its own KPIs, its pods' or its node's)."""
    if objs == code:
        return True
    named = cited_components(text, obj.get("cites"))
    if named is None:
        return False
    from .evidence import service_of

    for mine, theirs in zip(objs, code, strict=True):
        comp = mine[KEYS[1]]
        if comp == theirs[KEYS[1]]:
            continue
        own = {n for n in named if comp in (n, service_of(n), host.get(n)) or host.get(comp) == n}
        if not own:
            return False
    return True


def cmd_answers(a) -> None:
    """Validate the model's answers; fall back to the statistics answer when one is invalid or,
    in the `both` variant, changes the code answer without citing evidence for it."""
    work = Path(a.work)
    evs = load_evidence(work)
    rows, fallbacks, overridden = [], [], []
    for qid, ev in evs.items():
        path = work / a.answers / f"{qid}.json"
        code = stats_answer(ev)
        objs, raw = None, None
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                objs = validate(raw, ev["query"], known_components(ev))
            except (json.JSONDecodeError, UnicodeDecodeError):
                objs = None
        if objs is not None and a.variant == "both":
            text = request_text(qid, ev, candidates(ev), "both", code)
            if not override_ok(raw, objs, code, text, ev["host"]):
                objs = None
            elif objs != code:
                overridden.append(qid)
        if objs is None:
            fallbacks.append(qid)
            objs = code
        rows.append({"id": qid, "prediction": prediction_text(objs)})
    pd.DataFrame(rows).to_csv(work / a.out, index=False)
    print(
        f"{len(rows)} answers -> {work / a.out}; fallbacks {len(fallbacks)}: {fallbacks}; "
        f"code answer changed with citations {len(overridden)}: {overridden}"
    )


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
    e.add_argument("--system", choices=sorted(systems.SYSTEMS), default="Market")
    e.add_argument(
        "--skip-missing-days",
        action="store_true",
        help="leave out queries whose date has no reduced telemetry (a partial sample only)",
    )
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
    an.add_argument(
        "--variant",
        choices=["both", "model-only"],
        default="both",
        help="both: a changed code answer must cite evidence; model-only: no code answer shown",
    )
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
