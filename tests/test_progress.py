"""Tests for the log-stream progress parser behind the status bubble (0.7.25).

The bubble shows X-of-Y, ok/skip tallies, the part in work and the latest
line, all parsed from the same log lines the runners already emit. Lines here
are lifted verbatim from the real 0.7.22 140-part log so the regexes are
checked against production output, not hand-typed approximations.

Runnable with pytest or directly:  python -m tests.test_progress
"""

from __future__ import annotations

from autoboost.progress import Progress


def test_header_sets_total_index_name():
    p = Progress()
    p.feed("=== [37/140] 8640-2101-4 ===")
    assert (p.total, p.index, p.name) == (140, 37, "8640-2101-4")
    assert p.completed == 0


def test_tallies_from_ok_and_skip_lines():
    p = Progress()
    p.feed("=== [1/140] 8640-4109-1 ===")
    p.feed("  SKIP (done=0 skipped=1)")
    assert (p.done, p.skipped, p.completed) == (0, 1, 1)
    p.feed("=== [2/140] 8640-4109-2 ===")
    p.feed("  OK  (done=1 skipped=1)")
    assert (p.done, p.skipped, p.completed) == (1, 1, 2)


def test_multiline_feed_and_latest_line():
    p = Progress()
    p.feed("\n=== [8/140] 8763-1102-4 ===")
    p.feed("    zoom extents (z)\n    dimensions: '18.3 in x 92 in' -> (464.82, 2336.8)")
    assert p.name == "8763-1102-4"
    assert p.latest.startswith("dimensions:")


def test_total_from_job_line_before_any_part():
    p = Progress()
    p.feed("Running the 49 part(s) selected in the Home list.")
    assert p.total == 49
    p.feed("Full job: 49 part(s). Phases=stencil+cut")
    assert p.total == 49
    # A later part header is authoritative and does not fight the job line.
    p.feed("=== [3/49] 8640-2110-4 ===")
    assert (p.total, p.index) == (49, 3)


def test_finished_flags():
    p = Progress()
    p.feed("\nFull job complete: done=89, skipped=51, of 140")
    assert p.finished
    q = Progress()
    q.feed("===== END =====")
    assert q.finished


def test_summary_before_and_after_total():
    p = Progress()
    assert p.summary() == "starting..."
    p.feed("  3...")
    assert p.summary() == "3..."
    p.feed("=== [37/140] 8640-2101-4 ===")
    p.feed("  OK  (done=35 skipped=2)")
    s = p.summary()
    assert s.startswith("37/140  ok 35 · skip 2")
    assert "[37] 8640-2101-4" in s


def test_stamped_lines_do_not_confuse_it():
    # The GUI feeds the raw message, but a pasted stamped line must still parse.
    p = Progress()
    p.feed("[1:52:33.7] === [134/140] 8640213I-03 ===")
    assert (p.index, p.total, p.name) == (134, 140, "8640213I-03")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
