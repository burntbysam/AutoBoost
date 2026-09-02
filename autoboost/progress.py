"""Live job progress, parsed from the log stream.

The status bubble (gui.py) needs X-of-Y progress, ok/skip tallies, the part in
work, and the latest activity line. The runners already SAY all of that in
their log lines -- '=== [37/140] 8640-2101-4 ===', 'OK  (done=35 skipped=2)' --
so rather than threading counters through every runner, the GUI feeds each
line to Progress.feed() and renders the result. Pure logic, no tkinter, so it
is unit-tested without a display (tests/test_progress.py).
"""

from __future__ import annotations

import re

_PART = re.compile(r"=== \[(\d+)/(\d+)\] (\S+) ===")
_TALLY = re.compile(r"\b(?:OK|SKIP)\s*\(done=(\d+) skipped=(\d+)\)")
_TOTAL = re.compile(r"\b(\d+) part\(s\)")
_END_PREFIXES = ("Full job complete", "Job complete", "Cut job complete")


class Progress:
    """Accumulates job state from log lines. Feed every line (multi-line
    strings fine); read total/index/name/done/skipped/latest/finished."""

    def __init__(self):
        self.total = 0
        self.index = 0
        self.name = ""
        self.done = 0
        self.skipped = 0
        self.latest = ""
        self.finished = False

    @property
    def completed(self) -> int:
        return self.done + self.skipped

    def feed(self, text) -> None:
        for line in str(text).split("\n"):
            line = line.strip()
            if not line:
                continue
            self.latest = line
            m = _PART.search(line)
            if m:
                self.index = int(m.group(1))
                self.total = int(m.group(2))
                self.name = m.group(3)
                continue
            m = _TALLY.search(line)
            if m:
                self.done, self.skipped = int(m.group(1)), int(m.group(2))
                continue
            if self.total == 0:
                m = _TOTAL.search(line)
                if m:
                    self.total = int(m.group(1))
            if "===== END =====" in line or line.startswith(_END_PREFIXES):
                self.finished = True

    def summary(self) -> str:
        """One line for the bubble label: tallies, part in work, latest line."""
        if not self.total:
            return self.latest or "starting..."
        head = f"{self.completed}/{self.total}  ok {self.done} · skip {self.skipped}"
        cur = f"  [{self.index}] {self.name}" if self.name else ""
        tail = f"  —  {self.latest}" if self.latest else ""
        return head + cur + tail
