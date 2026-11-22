"""Turn fault flags on and off in the demo shop's flagd file.

    python -m live.inject set FLAG VARIANT        # development runs only
    python -m live.inject reset                   # every fault flag back to off
    python -m live.inject sealed --schedule F --index I --log L
        # apply entry I of a sealed schedule without printing it, record the time in L

The sealed form exists so the person running the agent never sees which fault is live: the
schedule was written by a separate agent, and this command prints only "injected".
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .source import SERVICE_FLAGS

FLAG_FILE = Path("/home/user/otel-demo/src/flagd/demo.flagd.json")


def write(doc: dict) -> None:
    with open(FLAG_FILE, "r+") as f:
        f.seek(0)
        f.write(json.dumps(doc, indent=2))
        f.truncate()


def set_flag(name: str, variant: str) -> None:
    doc = json.loads(FLAG_FILE.read_text())
    if name not in doc["flags"] or variant not in doc["flags"][name]["variants"]:
        raise SystemExit("unknown flag or variant")
    doc["flags"][name]["defaultVariant"] = variant
    write(doc)


def reset() -> None:
    doc = json.loads(FLAG_FILE.read_text())
    for flags in SERVICE_FLAGS.values():
        for name in flags:
            doc["flags"][name]["defaultVariant"] = "off"
    write(doc)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("flag")
    s.add_argument("variant")
    sub.add_parser("reset")
    e = sub.add_parser("sealed")
    e.add_argument("--schedule", required=True)
    e.add_argument("--index", type=int, required=True)
    e.add_argument("--log", required=True)
    a = p.parse_args()
    if a.cmd == "set":
        set_flag(a.flag, a.variant)
    elif a.cmd == "reset":
        reset()
    else:
        entry = json.loads(Path(a.schedule).read_text())["incidents"][a.index]
        reset()
        set_flag(entry["flag"], entry["variant"])
        with open(a.log, "a") as f:
            f.write(json.dumps({"index": a.index, "injected_at": time.time()}) + "\n")
        print(json.dumps({"injected": a.index, "open_after_s": entry["open_after_s"]}))


if __name__ == "__main__":
    main()
