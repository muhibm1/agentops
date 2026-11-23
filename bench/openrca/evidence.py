"""Turn one OpenRCA query into scored evidence: how every component's KPIs moved inside the query
window, measured against the same day outside it.

Inputs are the per-minute tables `reduce.py` wrote for the query's date. Every KPI of every
component becomes one per-minute series, tagged with the component, the level it belongs to
(pod, service or node) and a kind:

    cpu, mem, disk-read, disk-write, disk-space, disk-io, net, process   (resource KPIs)
    latency, error, load                                                  (traffic KPIs)

The component names come from the telemetry itself (`cmdb_id` and the service names in the
service metrics). No list of answer components is used anywhere: a pod's service and a node are
derived from the names the telemetry prints (`node-6.cartservice-0` is pod `cartservice-0` on
`node-6`; pod `cartservice-0` belongs to service `cartservice`).

Scoring reuses the frozen RCAEval statistics unchanged (`rcaeval.evidence._robust` through
`rcaeval.spans._signal`): a robust median and spread of the baseline, the shift of the window's
median and of its peak in baseline spreads, the direction, the first minute out of the 3-spread
band, and the after/before ratio. It runs twice: pass 1 splits at the window start; pass 2
re-scores from the detected onset to the end of the window, so a short fault late in the window is
not diluted by the quiet minutes before it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from rcaeval.evidence import MetricSignal
from rcaeval.spans import _signal

from .query import Query, to_epoch

# Metric name -> kind. First match wins. KPIs not listed (limits, specs, constants) are ignored.
CONTAINER_KINDS: list[tuple[str, str]] = [
    (r"^container_cpu_(usage|user|system)_seconds$|^container_cpu_cfs_throttled_seconds$", "cpu"),
    (r"^container_cpu_load_average_10s$", "cpu"),
    (r"^container_memory_(usage_MB|working_set_MB|rss|cache|failcnt)$", "mem"),
    (r"^container_fs_(reads|reads_MB)\./dev/vda$", "disk-read"),
    (r"^container_fs_(sector_reads|read_seconds)", "disk-read"),
    (r"^container_fs_(writes|writes_MB)\./dev/vda$", "disk-write"),
    (r"^container_fs_(sector_writes|write_seconds)", "disk-write"),
    (r"^container_network_", "net"),
    (r"^container_(threads|sockets|file_descriptors|last_seen|start_time_seconds)$", "process"),
    (r"^container_tasks_state\.(running|sleeping|uninterruptible|iowaiting)$", "process"),
]
NODE_KINDS: list[tuple[str, str]] = [
    (r"^system\.cpu\.(pct_usage|user|system|iowait)$|^system\.load\.1$", "cpu"),
    (r"^system\.mem\.(used|pct_usage|real\.used|real\.pct_useage|free|usable)$", "mem"),
    (r"^system\.io\.(r_s|rkb_s|r_await)$", "disk-read"),
    (r"^system\.io\.(w_s|w_await)$", "disk-write"),
    (r"^system\.io\.(util|avg_q_sz|await|svctm)$", "disk-io"),
    (r"^system\.disk\.(pct_usage|used|free)$|^system\.fs\.inodes\.(used|in_use)$", "disk-space"),
    (r"^system\.net\.udp\.in_errors$", "net-rx-error"),
    (r"^system\.net\.tcp\.retrans_segs$", "net-retrans"),
    (r"^system\.(net|tcp)\.", "net"),
]
# How each kind is weighted by the frozen RCAEval rank weights (KIND_WEIGHT keys).
WEIGHT_KEY = {
    "cpu": "cpu",
    "mem": "mem",
    "disk-read": "disk",
    "disk-write": "disk",
    "disk-space": "disk",
    "disk-io": "disk",
    "net": "net",
    "net-rx-error": "net",
    "net-retrans": "net",
    "process": "error",
    "latency": "latency",
    "error": "error",
    "load": "load",
}
# A second, local baseline (the two hours before the window) was measured on cloudbed-1 with the
# stronger of the two scores kept per series: strict 28 -> 27 of 70 alone, 30 -> 29 with the
# service and onset changes, so it is off (0). Set seconds here to turn it back on.
LOCAL_BASELINE_S = 0
RESOURCE = {
    "cpu",
    "mem",
    "disk-read",
    "disk-write",
    "disk-space",
    "disk-io",
    "net",
    "net-rx-error",
    "net-retrans",
    "process",
}

POD = re.compile(r"^(?P<svc>[a-z][a-z0-9-]*?)(?P<copy>\d*)-(?P<idx>\d+)$")
NODE = re.compile(r"^node-\d+$")


def service_of(pod: str) -> str | None:
    """The service a pod belongs to: `cartservice-1` and `cartservice2-0` -> `cartservice`."""
    m = POD.match(pod)
    return m.group("svc") if m else None


def level_of(comp: str) -> str:
    if NODE.match(comp):
        return "node"
    return "pod" if POD.match(comp) else "service"


def _kind(name: str, table: list[tuple[str, str]]) -> str | None:
    for pat, kind in table:
        if re.search(pat, name):
            return kind
    return None


@dataclass
class DayFrame:
    """Per-minute series for one date: `values` is minutes x series, `meta` one row per series."""

    date: str
    minutes: np.ndarray
    values: np.ndarray
    meta: pd.DataFrame  # columns: comp, level, kind, name, source
    host: dict[str, str] = field(default_factory=dict)  # pod -> node, from container cmdb ids
    log_templates: pd.DataFrame | None = None  # minute, comp, template, error, lines


def _pivot(df: pd.DataFrame, minutes: np.ndarray) -> tuple[np.ndarray, list[tuple]]:
    """df has columns minute, key (a tuple of meta), value; return minutes x keys array."""
    if df.empty:
        return np.empty((len(minutes), 0)), []
    w = df.pivot_table(index="minute", columns="key", values="value", aggfunc="mean")
    w = w.reindex(minutes)
    return np.array(w.to_numpy(dtype=float), copy=True), list(w.columns)


def load_day(reduced: Path, date: str) -> DayFrame:
    d = reduced / date
    m = pd.read_parquet(d / "metric.parquet")
    for c in ("file", "cmdb_id", "kpi_name"):
        m[c] = m[c].astype(str)
    y, mo, dd = (int(v) for v in date.split("_"))
    start = to_epoch(y, mo, dd, 0, 0)
    minutes = np.arange(start, start + 86400, 60, dtype=np.int64)
    parts: list[pd.DataFrame] = []
    host: dict[str, str] = {}

    def add(df: pd.DataFrame, comp, kind, name, source) -> None:
        if df.empty:
            return
        key = list(zip(comp, kind, name, [source] * len(df), strict=True))
        parts.append(
            pd.DataFrame(
                {
                    "minute": df["minute"].to_numpy(),
                    "key": key,
                    "value": df["value"].to_numpy(dtype=float),
                }
            )
        )

    # Container metrics: cmdb_id is "<node>.<pod>".
    c = m[m["file"] == "container"].copy()
    kinds = {k: _kind(k, CONTAINER_KINDS) for k in c["kpi_name"].unique()}
    c["kind"] = c["kpi_name"].map(kinds)
    c = c[c["kind"].notna()]
    split = c["cmdb_id"].str.split(".", n=1, expand=True)
    c["comp"] = split[1]
    for n, p in zip(split[0], split[1], strict=True):
        host.setdefault(p, n)
    add(c, c["comp"], c["kind"], c["kpi_name"], "container")

    nd = m[m["file"] == "node"].copy()
    kinds = {k: _kind(k, NODE_KINDS) for k in nd["kpi_name"].unique()}
    nd["kind"] = nd["kpi_name"].map(kinds)
    nd = nd[nd["kind"].notna()]
    add(nd, nd["cmdb_id"], nd["kind"], nd["kpi_name"], "node")

    # Service metrics: one row per service and minute (rr, sr, mrt, count).
    sv = m[m["file"] == "service"].copy()
    sv["comp"] = sv["cmdb_id"].str.replace(r"-(grpc|http)$", "", regex=True)
    sv["proto"] = sv["cmdb_id"].str.extract(r"-(grpc|http)$")[0].fillna("")
    sv = sv[sv["kpi_name"].isin(["sr", "mrt", "count"])]
    sv.loc[sv["kpi_name"] == "sr", "value"] = 100.0 - sv.loc[sv["kpi_name"] == "sr", "value"]
    skind = {"sr": "error", "mrt": "latency", "count": "load"}
    add(
        sv,
        sv["comp"],
        sv["kpi_name"].map(skind),
        "service_"
        + sv["kpi_name"].map({"sr": "fail_pct", "mrt": "mrt", "count": "count"})
        + "."
        + sv["proto"],
        "service",
    )

    # Mesh metrics: cmdb_id "<pod>.<direction>.<a>.<b>"; kpi "istio_<what>.<proto>.<http>.<grpc>.".
    me = m[m["file"] == "mesh"]
    me = me[
        me["kpi_name"].str.startswith(
            (
                "istio_request_duration_milliseconds",
                "istio_requests.",
                "istio_tcp_sent_bytes",
                "istio_tcp_received_bytes",
            )
        )
    ]
    me = me.assign(
        comp=me["cmdb_id"].str.split(".", n=1).str[0], direction=me["cmdb_id"].str.split(".").str[1]
    )
    codes = me["kpi_name"].str.extract(r"\.(grpc|http)\.(\d+)\.(\d*)")
    bad = (codes[1].fillna("200").str[0].isin(["0", "4", "5"])) | (
        codes[2].fillna("0").replace("", "0") != "0"
    )
    me = me.assign(bad=bad)
    dur = me[me["kpi_name"].str.startswith("istio_request_duration") & ~me["bad"]]
    dur = dur.groupby(["minute", "comp", "direction"], as_index=False)["value"].mean()
    add(dur, dur["comp"], ["latency"] * len(dur), "mesh_rt." + dur["direction"], "mesh")
    req = me[me["kpi_name"].str.startswith("istio_requests.")]
    err = req[req["bad"]].groupby(["minute", "comp"], as_index=False)["value"].sum()
    add(err, err["comp"], ["error"] * len(err), ["mesh_bad_requests"] * len(err), "mesh")
    tcp = me[me["kpi_name"].str.startswith("istio_tcp")]
    tcp = tcp.groupby(["minute", "comp"], as_index=False)["value"].sum()
    add(tcp, tcp["comp"], ["net"] * len(tcp), ["mesh_tcp_bytes"] * len(tcp), "mesh")
    del m

    tr = pd.read_parquet(d / "trace_comp.parquet")
    tr["comp"] = tr["comp"].astype(str)
    for col, kind in (
        ("self_p50", "latency"),
        ("dur_p95", "latency"),
        ("own_errors", "error"),
        ("spans", "load"),
    ):
        x = tr[["minute", "comp", col]].rename(columns={col: "value"})
        add(x, x["comp"], [kind] * len(x), ["trace_" + col] * len(x), "trace")

    lg = pd.read_parquet(d / "log_service.parquet")
    lg["comp"] = lg["comp"].astype(str)
    e = lg[lg["error"]].groupby(["minute", "comp"], as_index=False)["lines"].sum()
    e = e.rename(columns={"lines": "value"})
    add(e, e["comp"], ["error"] * len(e), ["log_error_lines"] * len(e), "log")

    px_path = d / "log_proxy.parquet"
    if px_path.exists():
        px = pd.read_parquet(px_path)
        px["comp"] = px["comp"].astype(str)
        ok = px["code"].astype(str).str.match(r"^[23]\d\d$") & (px["flags"].astype(str) == "-")
        http = px[px["code"].astype(str) != "none"]
        g = http.groupby(["minute", "comp"], as_index=False)[["lines", "dur_ms_sum"]].sum()
        g["value"] = g["dur_ms_sum"] / g["lines"].clip(lower=1)
        add(g, g["comp"], ["latency"] * len(g), ["proxy_mean_ms"] * len(g), "proxy")
        b = px[~ok & (px["code"].astype(str) != "none")]
        b = b[b["code"].astype(str) != "0"]
        b = b.groupby(["minute", "comp"], as_index=False)["lines"].sum()
        b = b.rename(columns={"lines": "value"})
        add(b, b["comp"], ["error"] * len(b), ["proxy_bad_responses"] * len(b), "proxy")

    df = pd.concat(parts, ignore_index=True)
    df = df[(df["minute"] >= minutes[0]) & (df["minute"] <= minutes[-1])]
    values, keys = _pivot(df, minutes)
    meta = pd.DataFrame(keys, columns=["comp", "kind", "name", "source"])
    meta["level"] = meta["comp"].map(level_of)
    # A count that is simply missing in a minute (no error lines, no bad responses) is zero.
    zero_fill = (
        meta["name"]
        .isin(
            [
                "log_error_lines",
                "proxy_bad_responses",
                "mesh_bad_requests",
                "trace_own_errors",
                "trace_spans",
            ]
        )
        .to_numpy()
    )
    values[:, zero_fill] = np.nan_to_num(values[:, zero_fill], nan=0.0)
    return DayFrame(date, minutes, values, meta, host, lg)


@dataclass
class Scored:
    """One series' movement in a query window, with its place in the frame.

    `sig` is the frozen RCAEval signal. `dom_up` and `dom_down` say how far the window's peak
    excursion above (below) the baseline median goes, in baseline spreads, relative to the
    largest excursion of any other half hour of the same day (floored at one spread): above 1
    means nothing else that day moved this series this far. `out_minutes` counts the window's
    minutes outside the 3-spread band in the signal's direction.
    """

    col: int
    comp: str
    level: str
    kind: str
    name: str
    sig: MetricSignal
    onset_min: int | None  # epoch seconds of the first sustained out-of-band minute
    dom_up: float = 0.0
    dom_down: float = 0.0
    out_minutes: int = 0
    baseline: str = "day"  # "day": the day outside the window; "local": the hours before it


def _sustained_onset(dev: np.ndarray, need: int) -> int | None:
    """Index of the first run of `need` consecutive minutes where `dev` is true."""
    run = 0
    for i, o in enumerate(dev):
        run = run + 1 if o else 0
        if run >= need:
            return i - need + 1
    return None


def _block_peaks(z: np.ndarray, t: np.ndarray, start: int, end: int) -> tuple[float, float]:
    """Largest z inside [start, end) and in any other half hour of the day (aligned blocks)."""
    inside = (t >= start) & (t < end)
    zin = z[inside]
    peak = float(np.nanmax(zin)) if np.isfinite(zin).any() else 0.0
    other = np.where(inside, np.nan, z)
    blocks = other[: len(other) // 30 * 30].reshape(-1, 30)
    with np.errstate(all="ignore"):
        bmax = np.nanmax(np.where(np.isfinite(blocks), blocks, -np.inf), axis=1)
    bmax = bmax[np.isfinite(bmax)]
    return peak, float(bmax.max()) if len(bmax) else 0.0


def score_window(
    day: DayFrame,
    start: int,
    end: int,
    after_from: int | None = None,
    sustain: int = 2,
    local_s: int = LOCAL_BASELINE_S,
) -> list[Scored]:
    """Score every series twice: against the day outside [start, end) and against the
    `local_s` seconds before `start`; after = [after_from, end). A series yields one `Scored` per
    baseline that has enough points; the ranking keeps the stronger of the two."""
    t = day.minutes
    inside = (t >= start) & (t < end)
    a0 = start if after_from is None else after_from
    after = (t >= a0) & (t < end)
    bases = [("day", ~inside)]
    if local_s > 0:
        bases.append(("local", (t >= start - local_s) & (t < start)))
    out: list[Scored] = []
    for j in range(day.values.shape[1]):
        x = day.values[:, j]
        xa = x[after]
        if np.isfinite(xa).sum() < 3:
            continue
        whole = x[np.isfinite(x)]
        if np.nanmax(whole) == np.nanmin(whole):
            continue
        xa_f = pd.Series(xa).ffill().bfill().to_numpy()
        r = day.meta.iloc[j]
        dom: tuple[float, float] | None = None  # from the day baseline, shared by both
        for bname, base in bases:
            xb = x[base]
            xb = xb[np.isfinite(xb)]
            if len(xb) < 10:
                continue
            sig = _signal(f"{r.comp}|{r['name']}", r.comp, r.kind, xb, xa_f, whole, 60)
            if sig is None:
                continue
            if dom is None:
                z = (x - sig.baseline_median) / sig.baseline_spread
                up_in, up_out = _block_peaks(z, t, a0, end)
                dn_in, dn_out = _block_peaks(-z, t, a0, end)
                dom = (max(up_in, 0.0) / max(up_out, 1.0), max(dn_in, 0.0) / max(dn_out, 1.0))
            za = (xa_f - sig.baseline_median) / sig.baseline_spread * sig.direction
            i = _sustained_onset(za > 3, sustain)
            onset = int(t[after][i]) if i is not None else None
            out.append(
                Scored(
                    j,
                    r.comp,
                    r.level,
                    r.kind,
                    r["name"],
                    sig,
                    onset,
                    dom_up=dom[0],
                    dom_down=dom[1],
                    out_minutes=int((za > 3).sum()),
                    baseline=bname,
                )
            )
    return out


# Any token that still carries a masked digit run (`#`), such as the remains of a UUID, a user or
# trace id, or a hex blob, is an identifier: it becomes one `#`, so lines that differ only by id
# share a template.
ID_TOKEN = re.compile(r"[\w-]*#[\w#-]*")


def mask_ids(template: str) -> str:
    """A log template with every identifier-like token collapsed to `#`."""
    return ID_TOKEN.sub("#", template)


def new_templates(day: DayFrame, start: int, end: int) -> dict[str, list[tuple[str, int]]]:
    """Per component, log templates (ids masked) printed inside the window and never outside it
    that day."""
    lg = day.log_templates
    if lg is None or lg.empty:
        return {}
    lg = lg.assign(template=lg["template"].astype(str).map(mask_ids))
    inside = (lg["minute"] >= start) & (lg["minute"] < end)
    seen = set(zip(lg.loc[~inside, "comp"], lg.loc[~inside, "template"], strict=True))
    win = lg[inside]
    mask = [k not in seen for k in zip(win["comp"], win["template"], strict=True)]
    win = win[mask]
    out: dict[str, list[tuple[str, int]]] = {}
    for (comp, tmpl), n in win.groupby(["comp", "template"])["lines"].sum().items():
        out.setdefault(comp, []).append((tmpl, int(n)))
    for k in out:
        out[k].sort(key=lambda x: -x[1])
    return out


def window_for(q: Query) -> tuple[int, int]:
    return q.start, q.end
