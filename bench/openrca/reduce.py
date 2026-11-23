"""Shrink one day of OpenRCA telemetry to small per-minute tables, once, so every query reads
megabytes instead of gigabytes.

A Market day is about 6 GB of CSV: traces (one row per span) and logs (one row per line) are most
of it. Nothing downstream needs single spans or lines, only how each component behaved minute by
minute, so this module writes, per date, under `<out>/<date>/`:

- `trace_comp.parquet`: per minute and component (the span's `cmdb_id`), the span count, the
  median, 95th percentile and mean duration, the median and mean self-time (a span's duration
  minus the time spent in its direct children, which does not propagate up the call chain), the
  spans whose status is not OK, and those errors that started there (no child span also failed).
- `trace_edge.parquet`: per minute, caller component and callee component, the call count, the
  callee spans' mean duration and their errors.
- `log_service.parquet`: per minute, component and message template (digits and hex ids
  masked), the line count. Lines that look like errors are flagged.
- `log_proxy.parquet`: per minute, component, response code and envoy response flags, the line
  count and the summed request duration in ms.
- `metric.parquet`: every metric CSV, unchanged in content, as one long table
  (minute, file, cmdb_id, kpi_name, value) that loads in a second.

Every CSV is read in blocks with pyarrow, single-threaded, so the reduction stays under a few
hundred MB of memory and leaves the rest of the machine alone. The raw files are never changed or
deleted here. No label file (`record.csv`, `scoring_points`) is read.
"""

from __future__ import annotations

import argparse
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv

pa.set_cpu_count(1)
pa.set_io_thread_count(1)

BLOCK = 64 << 20
OK_STATUS = ["0", "0.0", "200", "OK", "Ok", "ok", "Unset", "UNSET", ""]
MASK = r"[0-9a-fA-F]{8,}|\d+"
ERROR_WORDS = r"(?i)error|exception|fail|timeout|timed out|refused|unavailable|panic|fatal"
ENVOY = r'^"[^"]*" (?P<code>\d+) (?P<flags>\S+) \S+ \S+ "[^"]*" \d+ \d+ (?P<dur>\d+) '


def _reader(path: Path, columns: list[str], types: dict | None = None):
    return pcsv.open_csv(
        path,
        read_options=pcsv.ReadOptions(block_size=BLOCK, use_threads=False),
        convert_options=pcsv.ConvertOptions(include_columns=columns, column_types=types or {}),
    )


def _hash(arr: pa.ChunkedArray | pa.Array) -> np.ndarray:
    return pd.util.hash_array(np.asarray(arr.to_pandas(), dtype=object).astype(str))


def reduce_traces(path: Path, out: Path) -> None:
    cols = ["timestamp", "cmdb_id", "span_id", "duration", "status_code", "parent_span"]
    types = {"status_code": pa.string(), "parent_span": pa.string(), "span_id": pa.string()}
    fields = ("ts", "comp", "span", "par", "dur", "err")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in fields}
    names: dict[str, int] = {}
    for batch in _reader(path, cols, types):
        comp = batch.column("cmdb_id").to_pandas().astype(str)
        for c in comp.unique():
            names.setdefault(c, len(names))
        parts["comp"].append(comp.map(names).to_numpy(np.int32))
        parts["ts"].append(batch.column("timestamp").to_numpy(zero_copy_only=False))
        parts["dur"].append(batch.column("duration").to_numpy(zero_copy_only=False))
        parts["span"].append(_hash(batch.column("span_id")))
        par = pc.fill_null(batch.column("parent_span"), "")
        parts["par"].append(_hash(par))
        st = pc.fill_null(batch.column("status_code"), "")
        parts["err"].append(~pc.is_in(st, value_set=pa.array(OK_STATUS)).to_numpy(False))
    a = {k: np.concatenate(v) for k, v in parts.items()}
    del parts
    df = pd.DataFrame(a)
    del a
    child_sum = df.groupby("par", sort=False)["dur"].sum()
    df["self"] = (df["dur"] - df["span"].map(child_sum).fillna(0).to_numpy()).clip(lower=0)
    child_err = df.loc[df["err"], "par"].unique()
    df["own_err"] = df["err"] & ~df["span"].isin(child_err)
    comp_of_span = pd.Series(df["comp"].to_numpy(), index=df["span"].to_numpy())
    comp_of_span = comp_of_span[~comp_of_span.index.duplicated()]
    df["caller"] = df["par"].map(comp_of_span).fillna(-1).astype(np.int32)
    df["minute"] = (df["ts"] // 60000 * 60).astype(np.int64)
    inv = {v: k for k, v in names.items()}
    g = df.groupby(["minute", "comp"], sort=True)
    tab = pd.DataFrame(
        {
            "spans": g.size(),
            "dur_p50": g["dur"].median(),
            "dur_p95": g["dur"].quantile(0.95),
            "dur_mean": g["dur"].mean(),
            "self_p50": g["self"].median(),
            "self_mean": g["self"].mean(),
            "errors": g["err"].sum(),
            "own_errors": g["own_err"].sum(),
        }
    ).reset_index()
    tab["comp"] = tab["comp"].map(inv)
    tab.to_parquet(out / "trace_comp.parquet", index=False)
    e = df[df["caller"] >= 0]
    e = e[e["caller"] != e["comp"]]
    ge = e.groupby(["minute", "caller", "comp"], sort=True)
    edges = pd.DataFrame(
        {"calls": ge.size(), "dur_mean": ge["dur"].mean(), "errors": ge["err"].sum()}
    ).reset_index()
    edges["caller"] = edges["caller"].map(inv)
    edges["comp"] = edges["comp"].map(inv)
    edges.to_parquet(out / "trace_edge.parquet", index=False)


def _agg(tables: list[pa.Table], keys: list[str], sums: list[str]) -> pd.DataFrame:
    t = pa.concat_tables(tables, promote_options="permissive")
    return t.group_by(keys).aggregate([(s, "sum") for s in sums]).to_pandas()


def reduce_log_service(path: Path, out: Path) -> None:
    acc: list[pa.Table] = []
    cols = ["timestamp", "cmdb_id", "value"]
    for batch in _reader(path, cols, {"value": pa.string(), "cmdb_id": pa.string()}):
        v = pc.fill_null(batch.column("value"), "")
        v = pc.utf8_slice_codeunits(v, 0, 160)
        tmpl = pc.replace_substring_regex(v, pattern=MASK, replacement="#")
        err = pc.match_substring_regex(v, pattern=ERROR_WORDS)
        minute = pc.multiply(pc.divide(batch.column("timestamp"), 60), 60)
        t = pa.table(
            {
                "minute": minute,
                "comp": batch.column("cmdb_id"),
                "template": tmpl,
                "error": err,
                "n": pa.array(np.ones(batch.num_rows, np.int64)),
            }
        )
        acc.append(t.group_by(["minute", "comp", "template", "error"]).aggregate([("n", "sum")]))
        if len(acc) > 40:
            acc = [
                pa.Table.from_pandas(
                    _agg(acc, ["minute", "comp", "template", "error"], ["n_sum"]).rename(
                        columns={"n_sum_sum": "n_sum"}
                    )
                )
            ]
    df = _agg(acc, ["minute", "comp", "template", "error"], ["n_sum"])
    df = df.rename(columns={"n_sum_sum": "lines"})
    df.to_parquet(out / "log_service.parquet", index=False)


def reduce_log_proxy(path: Path, out: Path) -> None:
    acc: list[pa.Table] = []
    cols = ["timestamp", "cmdb_id", "value"]
    keys = ["minute", "comp", "code", "flags"]
    for batch in _reader(path, cols, {"value": pa.string(), "cmdb_id": pa.string()}):
        v = pc.fill_null(batch.column("value"), "")
        x = pc.extract_regex(v, pattern=ENVOY)
        code = pc.fill_null(pc.struct_field(x, "code"), "none")
        flags = pc.fill_null(pc.struct_field(x, "flags"), "none")
        dur = pc.cast(pc.fill_null(pc.struct_field(x, "dur"), "0"), pa.int64())
        minute = pc.multiply(pc.divide(batch.column("timestamp"), 60), 60)
        t = pa.table(
            {
                "minute": minute,
                "comp": batch.column("cmdb_id"),
                "code": code,
                "flags": flags,
                "n": pa.array(np.ones(batch.num_rows, np.int64)),
                "dur": dur,
            }
        )
        acc.append(t.group_by(keys).aggregate([("n", "sum"), ("dur", "sum")]))
        if len(acc) > 40:
            df = _agg(acc, keys, ["n_sum", "dur_sum"])
            df = df.rename(columns={"n_sum_sum": "n_sum", "dur_sum_sum": "dur_sum"})
            acc = [pa.Table.from_pandas(df, preserve_index=False)]
    df = _agg(acc, keys, ["n_sum", "dur_sum"])
    df = df.rename(columns={"n_sum_sum": "lines", "dur_sum_sum": "dur_ms_sum"})
    df.to_parquet(out / "log_proxy.parquet", index=False)


def reduce_metrics(metric_dir: Path, out: Path) -> None:
    frames = []
    for f in sorted(metric_dir.glob("metric_*.csv")):
        kind = f.stem.removeprefix("metric_")
        t = pcsv.read_csv(f, read_options=pcsv.ReadOptions(use_threads=False)).to_pandas()
        if kind == "service":
            # service,timestamp,rr,sr,mrt,count: one row per service and minute, four KPIs.
            t = t.melt(id_vars=["service", "timestamp"], var_name="kpi_name", value_name="value")
            t = t.rename(columns={"service": "cmdb_id"})
        t["file"] = kind
        t["minute"] = (t["timestamp"] // 60 * 60).astype(np.int64)
        frames.append(t[["minute", "file", "cmdb_id", "kpi_name", "value"]])
    df = pd.concat(frames, ignore_index=True)
    for c in ("file", "cmdb_id", "kpi_name"):
        df[c] = df[c].astype("category")
    df.to_parquet(out / "metric.parquet", index=False)


def reduce_day(day_dir: Path, out_root: Path, only: set[str] | None = None) -> None:
    out = out_root / day_dir.name
    out.mkdir(parents=True, exist_ok=True)
    steps = [
        ("metric", lambda: reduce_metrics(day_dir / "metric", out)),
        ("trace", lambda: reduce_traces(day_dir / "trace" / "trace_span.csv", out)),
        ("log_service", lambda: reduce_log_service(day_dir / "log" / "log_service.csv", out)),
        ("log_proxy", lambda: reduce_log_proxy(day_dir / "log" / "log_proxy.csv", out)),
    ]
    for name, fn in steps:
        if only and name not in only:
            continue
        t0 = time.time()
        fn()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 10
        print(f"{day_dir.name} {name}: {time.time() - t0:.0f}s, peak {rss} MB", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--telemetry", required=True, help="the system's telemetry/ folder")
    p.add_argument("--out", required=True)
    p.add_argument("--only", default="", help="comma list of metric,trace,log_service,log_proxy")
    a = p.parse_args()
    only = set(a.only.split(",")) if a.only else None
    for day in sorted(Path(a.telemetry).iterdir()):
        if day.is_dir():
            reduce_day(day, Path(a.out), only)


if __name__ == "__main__":
    main()
