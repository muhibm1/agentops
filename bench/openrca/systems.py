"""One adapter per OpenRCA system: its reason vocabulary, how its KPI names map to kinds, which
KPIs make up each reason's signature, and how its component names relate.

Reason vocabularies are the answer formats OpenRCA declares for each system, copied from the
`POSSIBLE ROOT CAUSE REASONS` lists in its prompt files (github.com/microsoft/OpenRCA,
`rca/baseline/rca_agent/prompt/basic_prompt_<System>.py`, fetched 2026-11-23). The candidate
component lists in those files are not used. KPI-kind tables and signatures for Bank and Telecom
were written from the KPI names alone (their telemetry schemas), before any of their answers were
seen, and nothing in them was tuned on their telemetry; Market's were developed on cloudbed-1.

The active system is process-wide: `run.py` sets it once (`use`) from `--system` at extraction
and from the stored evidence afterwards, and every stage reads it with `current()`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

KindTable = list[tuple[str, str]]
Signature = tuple[str | None, list[tuple[str, int]]]  # (level or None for any, [(pattern, dir)])


@dataclass
class System:
    name: str
    reasons: list[str]
    signatures: dict[str, Signature]
    # metric file stem (as `reduce.py` names it) -> KPI-name table; first match wins
    kinds: dict[str, KindTable]
    # wide app/service files: file stem -> {column: (kind, series name, transform)}
    app: dict[str, dict[str, tuple[str, str, Callable[[float], float] | None]]]
    level_of: Callable[[str], str]
    service_of: Callable[[str], str | None]
    component_help: str
    # host protocol counter kind -> pod-level reason it marks (needs pod -> node from telemetry)
    net_markers: dict[str, str] = field(default_factory=dict)
    _compiled: list[tuple[str, str | None, re.Pattern, int]] | None = None

    def compiled(self) -> list[tuple[str, str | None, re.Pattern, int]]:
        if self._compiled is None:
            self._compiled = [
                (reason, level, re.compile(pat), d)
                for reason, (level, pats) in self.signatures.items()
                for pat, d in pats
            ]
        return self._compiled


# ---------------------------------------------------------------------------------------- Market

MARKET_POD = re.compile(r"^(?P<svc>[a-z][a-z0-9-]*?)(?P<copy>\d*)-(?P<idx>\d+)$")
MARKET_NODE = re.compile(r"^node-\d+$")


def market_service_of(pod: str) -> str | None:
    """The service a pod belongs to: `cartservice-1` and `cartservice2-0` -> `cartservice`."""
    m = MARKET_POD.match(pod)
    return m.group("svc") if m else None


def market_level_of(comp: str) -> str:
    if MARKET_NODE.match(comp):
        return "node"
    return "pod" if MARKET_POD.match(comp) else "service"


MARKET_CONTAINER: KindTable = [
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
MARKET_NODE_KINDS: KindTable = [
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

MARKET = System(
    name="Market",
    reasons=[
        "container CPU load",
        "container memory load",
        "container network packet retransmission",
        "container network packet corruption",
        "container network latency",
        "container packet loss",
        "container process termination",
        "container read I/O load",
        "container write I/O load",
        "node CPU load",
        "node CPU spike",
        "node memory consumption",
        "node disk read I/O consumption",
        "node disk write I/O consumption",
        "node disk space consumption",
    ],
    # "node CPU spike" and "container packet loss" share their neighbours' signatures and are
    # told apart afterwards (`rank.final_reason`).
    signatures={
        "node CPU load": ("node", [(r"^system\.cpu\.(pct_usage|user)$", 1)]),
        "node memory consumption": ("node", [(r"^system\.mem\.(pct_usage|used|real\.used)$", 1)]),
        "node disk read I/O consumption": ("node", [(r"^system\.io\.(rkb_s|r_s)$", 1)]),
        "node disk write I/O consumption": ("node", [(r"^system\.io\.(w_s|w_await)$", 1)]),
        "node disk space consumption": ("node", [(r"^system\.disk\.(pct_usage|used)$", 1)]),
        "container CPU load": ("pod", [(r"^container_cpu_(usage|user)_seconds$", 1)]),
        "container memory load": (
            "pod",
            [(r"^container_memory_(rss|working_set_MB|usage_MB)$", 1)],
        ),
        "container read I/O load": ("pod", [(r"^container_fs_reads(_MB)?\./dev/vda$", 1)]),
        "container write I/O load": ("pod", [(r"^container_fs_writes(_MB)?\./dev/vda$", 1)]),
        "container network latency": (
            "pod",
            [(r"^trace_self_p50$", 1), (r"^mesh_rt\.", 1), (r"^proxy_mean_ms$", 1)],
        ),
        "container network packet corruption": (
            "pod",
            [(r"^(log_error_lines|mesh_bad_requests|proxy_bad_responses|trace_own_errors)$", 1)],
        ),
        "container network packet retransmission": (
            "pod",
            [(r"^container_network_(transmit|receive)_packets\.eth0$", 1)],
        ),
        "container process termination": (
            "pod",
            [(r"^container_threads$", -1), (r"^trace_spans$", -1)],
        ),
    },
    kinds={"container": MARKET_CONTAINER, "node": MARKET_NODE_KINDS},
    app={},  # Market's service file is read by `evidence._market_metrics`
    level_of=market_level_of,
    service_of=market_service_of,
    component_help=(
        "A component is a node (node-N), a pod (as printed in the telemetry) or a service (a pod "
        "name without its copy number and index, e.g. pod `xservice-1` and `xservice2-0` -> "
        "service `xservice`; answer the service when all its pods are faulty)."
    ),
    # Retransmissions rising without receive errors as "container packet loss" was measured and
    # dropped: it relabelled latency faults (cloudbed-1 reason 27 -> 26 of 55).
    net_markers={"net-rx-error": "container network packet corruption"},
)

# ------------------------------------------------------------------------------------------ Bank
# Every component is a host (or a docker container) named by `cmdb_id`; there is no pod/service
# hierarchy in the answer format, so no service merging.

BANK_KINDS: KindTable = [
    (r"^OSLinux-CPU_CPU_(CPUCpuUtil|CPUUserTime|CPUSysTime|CPULoad)$", "cpu"),
    (r"^OSLinux-CPU_CPU-\d+_SingleCpuUtil$", "cpu"),
    (r"^OSLinux-CPU_CPU_CPUWio$", "disk-io"),
    (r"_MEMORY_MEMORY_(MEMUsedMemPerc|NoCacheMemPerc|UserMem)$", "mem"),
    (r"_SWAP_SWAP_SWPTotSwapUsedPercent$", "mem"),
    (r"_LOCALDISK_.*_(DSKRead|DSKRTps)$", "disk-read"),
    (r"_LOCALDISK_.*_(DSKWrite|DSKWTps)$", "disk-write"),
    (r"_LOCALDISK_.*_(DSKPercentBusy|DSKAvgServ|DSKBps|DSKTps|DSKReadWrite)$", "disk-io"),
    (r"_FILESYSTEM_.*_(FSCapacity|FSUsedSpace|FSInodeUsedPercent)$", "disk-space"),
    (r"_NETWORK_.*_NETInErr(Prc)?$", "net-rx-error"),
    (r"_NETWORK_.*_NETOutErr(Prcc?)?$", "net-tx-error"),
    (r"_NETWORK_", "net"),
    (r"^JVM-Operating System_.*_JVM_CPULoad$", "jvm-cpu"),
    (r"^JVM-Memory_.*_HeapMemory(Usage|Used)$", "jvm-mem"),
    (r"^Tomcat-MEMORY_.*_(JVMMemoryUsedPercent|JVMUsedMemory)$", "jvm-mem"),
    (r"^JVM-Threads_|^Tomcat-Threads_.*_CurrentThreads?(Busy|Count)", "process"),
    (r"^Tomcat-Requests_.*_(ProcessingTime|MaxTime)RequestInfo$", "latency"),
    (r"^Tomcat-Requests_.*_ErrorCountRequestInfo$", "error"),
    (r"^Tomcat-Requests_.*_RequestCountRequestInfo$", "load"),
    (r"^Container-DOCKER_.*_CpuPercent$", "cpu"),
    (r"^Container-DOCKER_.*_(MemPercent|MemUsage)$", "mem"),
    (r"^Container-DOCKER_.*_Network(Rx|Tx)Bytes$", "net"),
    (r"^Mysql-.*_(ThreadsConnected|ThreadsRunning|Connections|Max Used Connections)$", "load"),
    (r"^Mysql-.*_(SlowQueries|Aborted Connects)$", "error"),
    (r"^redis-.*\((used_cpu_sys|used_cpu_user)\)$", "cpu"),
    (r"^redis-.*\((used_memory|used_memory_rss)\)$", "mem"),
    (r"^redis-.*\((connected_clients|blocked_clients|instantaneous_ops_per_sec)\)$", "load"),
    (r"^redis-.*\(rejected_connections\)$", "error"),
]

BANK = System(
    name="Bank",
    reasons=[
        "high CPU usage",
        "high memory usage",
        "network latency",
        "network packet loss",
        "high disk I/O read usage",
        "high disk space usage",
        "high JVM CPU load",
        "JVM Out of Memory (OOM) Heap",
    ],
    signatures={
        "high CPU usage": (
            None,
            [(r"^OSLinux-CPU_CPU_(CPUCpuUtil|CPUUserTime)$", 1), (r"_CpuPercent$", 1)],
        ),
        "high memory usage": (
            None,
            [(r"_MEMORY_MEMORY_(MEMUsedMemPerc|NoCacheMemPerc|UserMem)$", 1), (r"_MemPercent$", 1)],
        ),
        "network latency": (
            None,
            [
                (r"^trace_self_p50$", 1),
                (r"^trace_dur_p95$", 1),
                (r"_(ProcessingTime|MaxTime)RequestInfo$", 1),
            ],
        ),
        "network packet loss": (
            None,
            [
                (r"_NETWORK_.*_NET(In|Out)Err", 1),
                (r"^trace_own_errors$", 1),
                (r"_ErrorCountRequestInfo$", 1),
            ],
        ),
        "high disk I/O read usage": (None, [(r"_LOCALDISK_.*_(DSKRead|DSKRTps)$", 1)]),
        "high disk space usage": (None, [(r"_FILESYSTEM_.*_(FSCapacity|FSUsedSpace)$", 1)]),
        "high JVM CPU load": (None, [(r"_JVM_CPULoad$", 1)]),
        "JVM Out of Memory (OOM) Heap": (
            None,
            [(r"_HeapMemory(Usage|Used)$", 1), (r"_JVMMemoryUsedPercent$", 1)],
        ),
    },
    kinds={"container": BANK_KINDS},
    app={
        "app": {
            "sr": ("error", "app_fail_pct", lambda v: 100.0 - v),
            "mrt": ("latency", "app_mrt", None),
            "cnt": ("load", "app_count", None),
        }
    },
    level_of=lambda comp: "host",
    service_of=lambda comp: None,
    component_help="A component is a name as printed in the telemetry (`cmdb_id`).",
)

# --------------------------------------------------------------------------------------- Telecom
# Names carry their level: os_* hosts, docker_* containers, db_* databases, redis_* caches.

TELECOM_LEVEL = [(r"^os_", "node"), (r"^docker_", "pod"), (r"^db_", "service")]


def telecom_level_of(comp: str) -> str:
    return next((lvl for pat, lvl in TELECOM_LEVEL if re.match(pat, comp)), "other")


TELECOM_NODE: KindTable = [
    (r"^(CPU_util_pct|CPU_user_time|CPU_system_time|Processor_load_1_min)$", "cpu"),
    (r"^CPU_iowait_time$", "disk-io"),
    (r"^(Memory_used_pct|Memory_used|Swap_used_pct)$", "mem"),
    (r"^Disk_rd_(ios|kbs)$", "disk-read"),
    (r"^Disk_wr_(ios|kbs)$", "disk-write"),
    (r"^Disk_(io_util|await|avgqu_sz|svctm)$", "disk-io"),
    (r"^FS_(used_pct|used_space|max_util)$", "disk-space"),
    (r"^Received_errors_packets$", "net-rx-error"),
    (r"^Sent_errors_packets$", "net-tx-error"),
    (r"^ICMP_ping$", "latency"),
    (r"^(Incoming|Outgoing)_network_traffic$|^(Received|Sent)_(packets|queue)$", "net"),
    (r"^(Recv_total|Send_total|ss_total)$", "net"),
    (r"^(Num_of_processes|Num_of_running_processes|Zombie_Process)$", "process"),
]
TELECOM_CONTAINER: KindTable = [
    (r"^container_cpu_used$", "cpu"),
    (r"^container_(mem_used|fgc|fgct)$", "mem"),
    (r"^container_(thread_running|thread_used_pct|thread_total|thread_idle)$", "process"),
    (r"^container_session_used$", "load"),
]
TELECOM_DB: KindTable = [
    (r"^(CPU_Used_Pct)$", "cpu"),
    (r"^(MEM_Used_Pct|MEM_real_util|MEM_Used|PGA_Used_Pct)$", "mem"),
    (r"^(Sess_Connect|Sess_Active|Session_pct|Proc_Used_Pct|Proc_User_Used_Pct)$", "db-session"),
    (r"^(On_Off_State|tnsping_result_time|Hang)$", "db-state"),
    (r"^Row_Lock$", "error"),
    (r"^(Tbs_Used_Pct|New_Tbs_Used_Pct|TempTbs_Pct|UndoTbs_Pct|Used_Tbs_Size)$", "disk-space"),
    (r"^(Physical_Read_Per_Sec|SeqRead_Per_Sec|SctRead_Per_Sec)$", "disk-read"),
    (r"^(DFParaWrite_Per_Sec|LFParaWrite_Per_Sec|Redo_Per_Sec)$", "disk-write"),
    (r"^(DbTime|AWS|ACS|AIOS)$", "latency"),
    (r"_Per_Sec$|^User_Commit$", "load"),
]
TELECOM_REDIS: KindTable = [
    (r"^used_cpu_(sys|user)$", "cpu"),
    (r"^used_memory(_rss)?$|^mem_fragmentation_ratio$", "mem"),
    (r"^(connected_clients|blocked_clients|rejected_connections)$", "db-session"),
    (r"^redis_ping$", "db-state"),
    (r"^(instantaneous_ops_per_sec|total_commands_processed)$", "load"),
    (r"^instantaneous_(input|output)_kbps$", "net"),
]

TELECOM = System(
    name="Telecom",
    reasons=["CPU fault", "network delay", "network loss", "db connection limit", "db close"],
    signatures={
        "CPU fault": (
            None,
            [(r"^(CPU_util_pct|CPU_user_time|container_cpu_used|CPU_Used_Pct)$", 1)],
        ),
        "network delay": (
            None,
            [(r"^trace_self_p50$", 1), (r"^trace_dur_p95$", 1), (r"^ICMP_ping$", 1)],
        ),
        "network loss": (
            None,
            [(r"^(Received|Sent)_errors_packets$", 1), (r"^trace_own_errors$", 1)],
        ),
        "db connection limit": (
            None,
            [(r"^(Sess_Connect|Sess_Active|Session_pct|Proc_Used_Pct|Proc_User_Used_Pct)$", 1)],
        ),
        "db close": (
            None,
            [
                (r"^On_Off_State$", -1),
                (r"^(Sess_Connect|Sess_Active)$", -1),
                (r"^tnsping_result_time$", 1),
            ],
        ),
    },
    kinds={
        "node": TELECOM_NODE,
        "container": TELECOM_CONTAINER,
        "service": TELECOM_DB,
        "middleware": TELECOM_REDIS,
    },
    app={
        "app": {
            "succee_rate": ("error", "app_fail_rate", lambda v: 1.0 - v),
            "avg_time": ("latency", "app_avg_time", None),
            "num": ("load", "app_count", None),
        }
    },
    level_of=telecom_level_of,
    service_of=lambda comp: None,
    component_help=(
        "A component is a name as printed in the telemetry (`cmdb_id`, or a database named by a "
        "trace span's `dsName`)."
    ),
)

SYSTEMS = {s.name: s for s in (MARKET, BANK, TELECOM)}
_current = [MARKET]


def use(name: str) -> System:
    """Make `name` the active system for this process."""
    _current[0] = SYSTEMS[name]
    return _current[0]


def current() -> System:
    return _current[0]
