"""Score ranked predictions the way RCAEval does: AC@k and Avg@k on the root-cause service.

Labels come only from the benchmark's own `cases.parquet`, read here and nowhere else.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd


def labels(data_dir: Path) -> dict[str, dict]:
    c = pd.read_parquet(data_dir / "cases.parquet")
    return {r.case: {"service": r.root_cause_service, "fault": r.fault, "dataset": r.dataset} for r in c.itertuples()}


def ac_at(ranking: list[str], truth: str, k: int) -> float:
    return 1.0 if truth in ranking[:k] else 0.0


def avg_at(ranking: list[str], truth: str, k: int = 5) -> float:
    return sum(ac_at(ranking, truth, j) for j in range(1, k + 1)) / k


def summarise(preds: dict[str, list[str]], lab: dict[str, dict], by: str = "dataset") -> pd.DataFrame:
    rows = []
    for case, ranking in preds.items():
        t = lab[case]
        rows.append(
            {
                "case": case,
                "dataset": t["dataset"],
                "fault": t["fault"],
                "AC@1": ac_at(ranking, t["service"], 1),
                "AC@3": ac_at(ranking, t["service"], 3),
                "Avg@5": avg_at(ranking, t["service"], 5),
            }
        )
    df = pd.DataFrame(rows)
    keys = [by] if by == "dataset" else ["dataset", by]
    out = df.groupby(keys)[["AC@1", "AC@3", "Avg@5"]].mean().round(3)
    out["n"] = df.groupby(keys).size()
    return out


def per_group(df_rows: list[dict]) -> dict:
    g = defaultdict(list)
    for r in df_rows:
        g[r["dataset"]].append(r)
    return g
