"""Shrink one day of OpenRCA telemetry to small per-minute tables, once, so every query reads
megabytes instead of gigabytes.

It reads every OpenRCA system (Market, Bank, Telecom): trace and wide metric columns are recognised
from each file's header, timestamps in milliseconds become seconds, and a file a system does not
have (Telecom has no logs, Bank no proxy log) is skipped. A Market day is about 6 GB of CSV:
traces (one row per span) and logs (one row per line) are most of it. Nothing downstream needs
single spans or lines, only how each component behaved minute by minute, so this module writes,
per date, under `<out>/<date>/`:

- `trace_comp.parquet`: per minute and component (the span's `cmdb_id`, and the database a span
  names as its callee, as Telecom's `dsName` does), the span count, the median, 95th percentile
  and mean duration, the median and mean self-time (a span's duration minus the time spent in its
  direct children, which does not propagate up the call chain), the spans whose status is not OK
  (or whose success flag is false), and those errors that started there (no child span also
  failed).
- `trace_edge.parquet`: per minute, caller component and callee component, the call count, the
  callee spans' mean duration and their errors.
- `log_service.parquet`: per minute, component and message template (digits and hex ids
  masked), the line count. Lines that look like errors are flagged.
- `log_proxy.parquet`: per minute, component, response code and envoy response flags, the line
  count and the summed request duration in ms.
- `metric.parquet`: every metric CSV, unchanged in content, as one long table
  (minute, file, cmdb_id, kpi_name, value) that loads in a second; wide app files are melted.

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
    """Block reader over `columns`. A malformed row (such as the cut last line of a file
    downloaded in part) is skipped and counted, never guessed at."""
    return pcsv.open_csv(
        path,
        read_options=pcsv.ReadOptions(block_size=BLOCK, use_threads=False),
        parse_options=pcsv.ParseOptions(invalid_row_handler=_skip_row),
        convert_options=pcsv.ConvertOptions(include_columns=columns, column_types=types or {}),
    )


SKIPPED = [0]


def _skip_row(row) -> str:
    SKIPPED[0] += 1
    return "skip"


def _header(path: Path) -> list[str]:
    with open(path, newline="") as f:
        return f.readline().strip().lstrip("\ufeff").split(",")


def _to_seconds(ts: np.ndarray) -> np.ndarray:
    """Epoch timestamps in seconds; a column in milliseconds (values past 1e11) is divided."""
    ts = np.asarray(ts, dtype=np.float64)
    if len(ts) and np.nanmax(ts) > 1e11:
        ts = ts / 1000.0
    return ts


# Trace column layouts by system, recognised from the file header: timestamp, component, span id,
# parent span id, duration, and how a failed span is marked (a status code, a success flag, or
# nothing), plus the callee a span names, if any (Telecom's `dsName`, the database it calls).
TRACE_LAYOUTS = [
    {
        "ts": "timestamp",
        "comp": "cmdb_id",
        "span": "span_id",
        "par": "parent_span",
        "dur": "duration",
        "status": "status_code",
    },
    {
        "ts": "startTime",
        "comp": "cmdb_id",
        "span": "id",
        "par": "pid",
        "dur": "elapsedTime",
        "ok": "success",
        "callee": "dsName",
    },
    {
        "ts": "timestamp",
        "comp": "cmdb_id",
        "span": "span_id",
        "par": "parent_id",
        "dur": "duration",
    },
]


def trace_layout(path: Path) -> dict[str, str]:
    head = set(_header(path))
    for lay in TRACE_LAYOUTS:
        if set(lay.values()) <= head:
            return lay
    raise ValueError(f"{path}: unknown trace columns {sorted(head)}")


def _hash(arr: pa.ChunkedArray | pa.Array) -> np.ndarray:
    return pd.util.hash_array(np.asarray(arr.to_pandas(), dtype=object).astype(str))


def reduce_traces(path: Path, out: Path) -> None:
    lay = trace_layout(path)
    cols = list(lay.values())
    keys = ("comp", "span", "par", "status", "ok", "callee")
    types = {lay[k]: pa.string() for k in keys if k in lay}
    fields = ("ts", "comp", "span", "par", "dur", "err", "callee")
    parts: dict[str, list[np.ndarray]] = {k: [] for k in fields}
    names: dict[str, int] = {"": -1}
    for batch in _reader(path, cols, types):
        comp = pc.fill_null(batch.column(lay["comp"]), "").to_pandas().astype(str)
        for c in comp.unique():
            names.setdefault(c, len(names) - 1)
        parts["comp"].append(comp.map(names).to_numpy(np.int32))
        parts["ts"].append(batch.column(lay["ts"]).to_numpy(zero_copy_only=False))
        parts["dur"].append(batch.column(lay["dur"]).to_numpy(zero_copy_only=False))
        parts["span"].append(_hash(pc.fill_null(batch.column(lay["span"]), "")))
        par = pc.fill_null(batch.column(lay["par"]), "")
        parts["par"].append(_hash(par))
        if "status" in lay:
            st = pc.fill_null(batch.column(lay["status"]), "")
            err = ~pc.is_in(st, value_set=pa.array(OK_STATUS)).to_numpy(False)
        elif "ok" in lay:
            ok = pc.utf8_lower(pc.fill_null(batch.column(lay["ok"]), "true"))
            err = ~pc.is_in(ok, value_set=pa.array(["true", "1", "1.0"])).to_numpy(False)
        else:
            err = np.zeros(batch.num_rows, dtype=bool)
        parts["err"].append(err)
        if "callee" in lay:
            cal = pc.fill_null(batch.column(lay["callee"]), "").to_pandas().astype(str)
            for c in cal.unique():
                names.setdefault(c, len(names) - 1)
            parts["callee"].append(cal.map(names).to_numpy(np.int32))
        else:
            parts["callee"].append(np.full(batch.num_rows, -1, np.int32))
    a = {k: np.concatenate(v) for k, v in parts.items()}
    del parts
    df = pd.DataFrame(a)
    del a
    df = df[df["comp"] >= 0]
    df["dur"] = df["dur"].astype(float)
    child_sum = df.groupby("par", sort=False)["dur"].sum()
    df["self"] = (df["dur"] - df["span"].map(child_sum).fillna(0).to_numpy()).clip(lower=0)
    child_err = df.loc[df["err"], "par"].unique()
    df["own_err"] = df["err"] & ~df["span"].isin(child_err)
    # A span that names its callee (a database) also counts as a span of that callee, with its
    # whole duration as the callee's own time, called by the span's component.
    called = df[df["callee"] >= 0]
    if len(called):
        called = called.copy()
        called["caller_comp"] = called["comp"]
        called["comp"] = called["callee"]
        called["self"] = called["dur"]
        called["own_err"] = called["err"]
    comp_of_span = pd.Series(df["comp"].to_numpy(), index=df["span"].to_numpy())
    comp_of_span = comp_of_span[~comp_of_span.index.duplicated()]
    df["caller"] = df["par"].map(comp_of_span).fillna(-1).astype(np.int32)
    if len(called):
        called = called.assign(caller=called["caller_comp"]).drop(columns=["caller_comp"])
        df = pd.concat([df, called[df.columns]], ignore_index=True)
    df["minute"] = (_to_seconds(df["ts"].to_numpy()) // 60 * 60).astype(np.int64)
    inv = {v: k for k, v in names.items() if v >= 0}
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


def _minute(ts: pa.Array) -> pa.Array:
    """Start of the minute, in epoch seconds, of integer timestamps in seconds or milliseconds."""
    secs = _to_seconds(ts.to_numpy(zero_copy_only=False))
    return pa.array((secs // 60 * 60).astype(np.int64))


def reduce_log_service(path: Path, out: Path) -> None:
    acc: list[pa.Table] = []
    cols = ["timestamp", "cmdb_id", "value"]
    for batch in _reader(path, cols, {"value": pa.string(), "cmdb_id": pa.string()}):
        v = pc.fill_null(batch.column("value"), "")
        v = pc.utf8_slice_codeunits(v, 0, 160)
        tmpl = pc.replace_substring_regex(v, pattern=MASK, replacement="#")
        err = pc.match_substring_regex(v, pattern=ERROR_WORDS)
        t = pa.table(
            {
                "minute": _minute(batch.column("timestamp")),
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
        t = pa.table(
            {
                "minute": _minute(batch.column("timestamp")),
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


# Wide app/service files: one row per service and time, one column per KPI. The id and time
# columns by system: Market `service`/`timestamp`, Bank `tc`/`timestamp`, Telecom
# `serviceName`/`startTime`.
WIDE_IDS = ("service", "tc", "serviceName")
WIDE_TIMES = ("timestamp", "startTime")


def reduce_metrics(metric_dir: Path, out: Path) -> None:
    """Every metric CSV as one long table (minute, file, cmdb_id, kpi_name, value). Long files
    (`cmdb_id`, `kpi_name` or `name`, `value`) keep their rows; wide app/service files are
    melted, one KPI per column. Timestamps in milliseconds become seconds."""
    frames = []
    for f in sorted(metric_dir.glob("metric_*.csv")):
        kind = f.stem.removeprefix("metric_")
        t = pcsv.read_csv(
            f,
            read_options=pcsv.ReadOptions(use_threads=False),
            parse_options=pcsv.ParseOptions(invalid_row_handler=_skip_row),
        ).to_pandas()
        cols = set(t.columns)
        if {"cmdb_id", "value"} <= cols and ("kpi_name" in cols or "name" in cols):
            t = t.rename(columns={"name": "kpi_name"})
            tcol = "timestamp"
        else:
            sid = next(c for c in WIDE_IDS if c in cols)
            tcol = next(c for c in WIDE_TIMES if c in cols)
            t = t.melt(id_vars=[sid, tcol], var_name="kpi_name", value_name="value")
            t = t.rename(columns={sid: "cmdb_id"})
        t["value"] = pd.to_numeric(t["value"], errors="coerce")
        t["file"] = kind
        t["minute"] = (_to_seconds(t[tcol].to_numpy()) // 60 * 60).astype(np.int64)
        frames.append(t[["minute", "file", "cmdb_id", "kpi_name", "value"]])
    df = pd.concat(frames, ignore_index=True)
    for c in ("file", "cmdb_id", "kpi_name"):
        df[c] = df[c].astype(str).astype("category")
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
    files = {
        "trace": day_dir / "trace" / "trace_span.csv",
        "log_service": day_dir / "log" / "log_service.csv",
        "log_proxy": day_dir / "log" / "log_proxy.csv",
    }
    for name, fn in steps:
        if only and name not in only:
            continue
        if name in files and not files[name].exists():
            print(f"{day_dir.name} {name}: no file, skipped", flush=True)
            continue
        t0 = time.time()
        fn()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 10
        print(
            f"{day_dir.name} {name}: {time.time() - t0:.0f}s, peak {rss} MB, "
            f"malformed rows skipped so far {SKIPPED[0]}",
            flush=True,
        )


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
