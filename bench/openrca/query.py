"""Read an OpenRCA query's `instruction` text into the facts the method needs.

Only the instruction is read. The `scoring_points` column holds the answer and is opened by the
scorer alone (`score.py`), never here.

Each instruction names a date, a 30-minute window, how many failures happened in it, and which of
the three answer fields are asked for (occurrence time, component, reason). Query times are local
time of the benchmark (UTC+8). Telemetry timestamps are epoch seconds (trace spans in
milliseconds). The conversion always uses the fixed +8 hour offset, never the machine's zone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
MONTH_NAMES = (
    "january february march april may june july august september october november december"
)
MONTHS = {m: i + 1 for i, m in enumerate(MONTH_NAMES.split())}
COUNT_WORDS = {"a": 1, "an": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5}
DATE = re.compile(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})")
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
COUNT = re.compile(
    r"\b(a|an|one|single|two|three|four|five|\d+)\s+(?:known\s+)?failures?\b", re.IGNORECASE
)
# Phrases that contain "time" but describe the window, not the asked field.
WINDOW_WORDS = re.compile(r"time\s*(range|period|frame)|timeframe", re.IGNORECASE)
FIELDS = ("datetime", "component", "reason")


@dataclass(frozen=True)
class Query:
    task: str
    start: int  # epoch seconds, window start
    end: int  # epoch seconds, window end
    failures: int
    fields: tuple[str, ...]  # subset of FIELDS, in that order

    @property
    def date(self) -> str:
        """The telemetry folder name (local date of the window start), e.g. 2022_03_20."""
        return datetime.fromtimestamp(self.start, TZ).strftime("%Y_%m_%d")


def to_epoch(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=TZ).timestamp())


def to_local(epoch: float) -> str:
    """Format epoch seconds as the benchmark's answer datetime string (UTC+8)."""
    return (
        datetime.fromtimestamp(int(round(epoch)), UTC).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
    )


def parse(task: str, instruction: str) -> Query:
    dates = DATE.findall(instruction)
    if not dates:
        raise ValueError(f"{task}: no date in instruction")
    first = dates[0]
    y, mo, d = int(first[2]), MONTHS[first[0].lower()], int(first[1])
    clocks = CLOCK.findall(instruction)
    if len(clocks) < 2:
        raise ValueError(f"{task}: no time window in instruction")
    start = to_epoch(y, mo, d, int(clocks[0][0]), int(clocks[0][1]))
    # The end can be on the next day ("from 23:30 to March 22, 2022, at 00:00").
    ey, emo, ed = (
        (int(dates[1][2]), MONTHS[dates[1][0].lower()], int(dates[1][1]))
        if len(dates) > 1
        else (y, mo, d)
    )
    end = to_epoch(ey, emo, ed, int(clocks[1][0]), int(clocks[1][1]))
    if end <= start:
        end += 86400
    m = COUNT.search(instruction)
    n = 1
    if m:
        w = m.group(1).lower()
        n = int(w) if w.isdigit() else COUNT_WORDS[w]
    text = WINDOW_WORDS.sub(" ", instruction)
    asked = []
    if re.search(r"\b(date)?time\b|occurrence|occurred|when", text, re.IGNORECASE):
        asked.append("datetime")
    if re.search(r"\bcomponents?\b", text, re.IGNORECASE):
        asked.append("component")
    if re.search(r"\breasons?\b", text, re.IGNORECASE):
        asked.append("reason")
    if not asked:
        raise ValueError(f"{task}: no asked field found")
    return Query(task, start, end, n, tuple(asked))
