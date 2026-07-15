"""AutoBoost control panel: run the jobs from a window instead of the console.

Start/Cancel buttons, a live log pane, and a Save Log button -- everything the
console runners print lands in the window instead. Under the hood it drives the
exact same job loop as the CLI runners (`full_runner.run_full_job`), so the
duplicate guard, consecutive-failure auto-stop, and end-of-run summary are
identical:

    mode "Stencil + cut"  ==  py -m autoboost.full_runner
    mode "Stencil only"   ==  py -m autoboost.stencil_runner
    mode "Cut only"       ==  py -m autoboost.cut_runner

Launch (on the workstation):

    py  -m autoboost.gui       # with a console behind it
    pyw -m autoboost.gui       # windowless -- the log pane IS the console

Cancel is graceful, like the 'q' kill switch: the run halts before the NEXT
part, so the current part always finishes (or recovers to Home) and nothing is
left half-done. Ctrl+C in a console / holding 'q' still work as backstops.

On every launch the panel checks for a newer version (git fetch of this
branch) and offers to install it. If the check can't run -- no network, git
trouble, anything -- it just says "Version check failed" and the tool works
as-is; updating is never required to run a job.

The font is fixed to the shop standard (EasyType-L=10mm) and the angular
positions to the last option -- the values every validated run has used. The
CLI runners still take --font / --angular for calibration work.

Built on tkinter (ships with Python) -- nothing new to install.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time

# pywinauto's UIA backend runs on COM. The job runs in a worker thread (the Tk
# main loop must stay responsive), and pywinauto's supported mode for that is
# STA. Must be set before comtypes/pywinauto are first imported, which happens
# lazily inside the worker.
sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __release__
from . import updater

DEFAULT_FONT = "EasyType-L=10mm"


def format_elapsed(seconds: float) -> str:
    """Elapsed-time stamp for log lines: [mm:ss.t], hours added if a run gets
    that long. Tenths are enough -- the queue poll batches at 100ms anyway."""
    tenths = int(round(max(0.0, seconds) * 10))   # round FIRST or 59.96 -> 00:60.0
    m, s = divmod(tenths / 10, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"[{h}:{m:02d}:{s:04.1f}]"
    return f"[{int(m):02d}:{s:04.1f}]"


class _Done:
    """Sentinel the worker puts on the queue when the job ends."""

    def __init__(self, ok: bool):
        self.ok = ok


class _UpdateCheckDone:
    """Sentinel: the launch-time version check finished."""

    def __init__(self, check: updater.UpdateCheck):
        self.check = check


class _UpdateApplied:
    """Sentinel: the update attempt finished."""

    def __init__(self, ok: bool, msg: str):
        self.ok = ok
        self.msg = msg


class App:
    POLL_MS = 100  # how often the UI drains the worker's log queue

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(__release__)
        root.minsize(700, 540)
        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self._cancelled = False
        self._t0 = time.monotonic()   # program start; every log line is stamped
                                      # with the elapsed time since this moment
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append(f"{__release__} -- control panel")
        self._append("Put Boost on the Home screen, pick a job, hit Start.")
        root.after(self.POLL_MS, self._poll)
        self._start_update_check()

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        pad = dict(padx=8, pady=4)
        frm = ttk.Frame(self.root)
        frm.pack(fill="both", expand=True)
        self._inputs: list[tk.Widget] = []   # disabled while a job runs

        # --- job mode
        mode_row = ttk.LabelFrame(frm, text="Job")
        mode_row.pack(fill="x", **pad)
        self.mode = tk.StringVar(value="full")
        for text, val in (("Stencil + cut (full)", "full"),
                          ("Stencil only", "stencil"),
                          ("Cut only", "cut")):
            rb = ttk.Radiobutton(mode_row, text=text, value=val, variable=self.mode)
            rb.pack(side="left", padx=10, pady=4)
            self._inputs.append(rb)

        # --- parts
        parts_row = ttk.LabelFrame(
            frm, text="Parts  (blank = every part in the Home list)")
        parts_row.pack(fill="x", **pad)
        self.parts = tk.StringVar()
        parts_entry = ttk.Entry(parts_row, textvariable=self.parts)
        parts_entry.pack(fill="x", padx=8, pady=(4, 0))
        self._inputs.append(parts_entry)
        ttk.Label(parts_row, foreground="gray",
                  text="Separate with spaces or commas, e.g.  8604300I-1, 8604301I-1"
                  ).pack(anchor="w", padx=8, pady=(0, 4))

        # --- options (font + angular positions are fixed to the shop standard;
        # the CLI runners keep --font/--angular for calibration work)
        opt = ttk.LabelFrame(frm, text="Options")
        opt.pack(fill="x", **pad)
        self.max_failures = tk.StringVar(value="5")
        self.delay = tk.StringVar(value="5")

        def opt_field(col: int, label: str, var: tk.StringVar, width: int) -> None:
            ttk.Label(opt, text=label).grid(row=0, column=col, sticky="w",
                                            padx=(10, 2), pady=4)
            e = ttk.Entry(opt, textvariable=var, width=width)
            e.grid(row=1, column=col, sticky="w", padx=(10, 2), pady=(0, 4))
            self._inputs.append(e)

        opt_field(0, "Max consec. failures", self.max_failures, 6)
        opt_field(1, "Start delay (s)", self.delay, 6)

        # --- buttons
        btns = ttk.Frame(frm)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="Start", command=self._start)
        self.start_btn.pack(side="left", padx=(8, 4))
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self._cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        ttk.Button(btns, text="Save Log...", command=self._save_log
                   ).pack(side="right", padx=(4, 8))
        ttk.Button(btns, text="Clear Log", command=self._clear_log
                   ).pack(side="right", padx=4)

        # --- log pane
        self.log = ScrolledText(frm, height=18, state="disabled", wrap="word",
                                font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # --- status bar
        self.status = ttk.Label(frm, text="Idle.", anchor="w", relief="sunken")
        self.status.pack(fill="x", side="bottom", padx=0, pady=0, ipady=2)

    # ------------------------------------------------------------------- log

    def _append(self, msg: str) -> None:
        # Stamp every printed line with the elapsed time since launch, so a
        # saved log shows where the seconds go. Blank lines stay blank (the
        # ===== START banner leads with one).
        stamp = format_elapsed(time.monotonic() - self._t0)
        text = "\n".join(f"{stamp} {line}" if line.strip() else line
                         for line in str(msg).split("\n"))
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _save_log(self) -> None:
        text = self.log.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showinfo("AutoBoost", "The log is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Save log",
            defaultextension=".log",
            initialfile=time.strftime("autoboost_%Y%m%d_%H%M%S.log"),
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            self._append(f"[log saved to {path}]")
        except OSError as exc:
            messagebox.showerror("AutoBoost", f"Could not save the log:\n{exc}")

    # ---------------------------------------------------------- update check

    def _start_update_check(self) -> None:
        """Launch-time version check, off the UI thread. Best-effort by design:
        a failed check logs one line and the tool is fully usable regardless."""
        self._append("Checking for a newer version...")
        threading.Thread(
            target=lambda: self.q.put(_UpdateCheckDone(updater.check_for_update())),
            daemon=True).start()

    def _on_update_check(self, check: updater.UpdateCheck) -> None:
        if check.status == "failed":
            self._append(f"Version check failed ({check.detail}) -- "
                         "continuing with the current version.")
            return
        if check.status == "current":
            self._append("Version check: you are on the latest version.")
            return
        self._append(f"Version check: a newer version is available ({check.detail}).")
        if self.worker and self.worker.is_alive():
            self._append("A job is running -- update offered again on the next launch.")
            return
        if not messagebox.askyesno(
                "AutoBoost update",
                f"A newer version of AutoBoost is available\n({check.detail}).\n\n"
                "Install it now?"):
            self._append("Update declined -- staying on the current version.")
            return
        self._append("Installing the update...")
        self.start_btn.configure(state="disabled")
        threading.Thread(
            target=lambda: self.q.put(_UpdateApplied(*updater.apply_update())),
            daemon=True).start()

    def _on_update_applied(self, res: _UpdateApplied) -> None:
        if not (self.worker and self.worker.is_alive()):
            self.start_btn.configure(state="normal")
        if res.ok:
            self._append(res.msg)
            messagebox.showinfo(
                "AutoBoost update",
                "Update installed.\n\nClose and relaunch AutoBoost to run the "
                "new version.")
        else:
            self._append(f"{res.msg} -- continuing with the current version.")

    # ------------------------------------------------------------ start/cancel

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return  # button should be disabled anyway
        try:
            max_failures = max(1, int(self.max_failures.get()))
            delay = max(0, int(self.delay.get()))
        except ValueError:
            messagebox.showerror(
                "AutoBoost", "Max failures and start delay must be whole numbers.")
            return

        part_names = [p for p in re.split(r"[\s,;]+", self.parts.get().strip()) if p]
        mode = self.mode.get()
        params = dict(
            parts=part_names or None,
            font=DEFAULT_FONT,
            angular=None,
            do_stencil=(mode != "cut"),
            do_cut=(mode != "stencil"),
            max_failures=max_failures,
            delay=delay,
        )

        self._cancelled = False
        self._set_running(True)
        label = {"full": "stencil + cut", "stencil": "stencil only",
                 "cut": "cut only"}[mode]
        scope = f"{len(part_names)} listed part(s)" if part_names else "every part in the Home list"
        self._append(f"\n===== START: {label}, {scope} =====")
        self.status.configure(
            text=f"Running ({label})... Cancel stops before the next part.")
        self.worker = threading.Thread(target=self._worker, args=(params,),
                                       daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self._cancelled = True
        self.cancel_btn.configure(state="disabled")
        self.status.configure(text="Cancelling -- finishing the current part...")
        self._append("[cancel requested -- the run stops before the next part]")
        try:
            # If the worker is still importing the stack, this waits for the
            # import lock and then sets the flag -- still ahead of the job loop.
            from .stencil_runner import request_stop
            request_stop()
        except Exception:
            pass  # the worker's own import failed too; it never started the job

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for w in self._inputs:
            w.configure(state=state)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")

    # ---------------------------------------------------------------- worker

    def _worker(self, params: dict) -> None:
        """Runs in a background thread. Only talks to the UI via the queue."""
        log = self.q.put
        try:
            try:
                import comtypes
                comtypes.CoInitialize()   # COM for pywinauto, in THIS thread
            except Exception:
                pass
            from .stencil_runner import STOP
            from .full_runner import run_full_job
        except Exception as exc:  # noqa: BLE001 - report into the log pane
            log(f"Could not load the automation stack: {exc!r}")
            log("On the workstation run:  pip install --user -r requirements.txt")
            self.q.put(_Done(False))
            return

        STOP.clear()
        try:
            if params["delay"]:
                log(f"Starting in {params['delay']}s -- put Boost on the Home screen.")
                for i in range(params["delay"], 0, -1):
                    if STOP.is_set():
                        log("Cancelled during countdown -- nothing was run.")
                        self.q.put(_Done(False))
                        return
                    log(f"  {i}...")
                    time.sleep(1)
            t0 = time.time()
            ok = run_full_job(part_names=params["parts"],
                              target_font=params["font"],
                              angular=params["angular"],
                              do_stencil=params["do_stencil"],
                              do_cut=params["do_cut"],
                              max_consecutive_failures=params["max_failures"],
                              log=log)
            log(f"Elapsed: {time.time() - t0:.0f}s")
            self.q.put(_Done(ok))
        except ImportError as exc:
            # pywinauto imports lazily inside BoostUIA(), so a missing package
            # surfaces here rather than at module import above.
            log(f"Missing dependency: {exc!r}")
            log("On the workstation run:  pip install --user -r requirements.txt")
            self.q.put(_Done(False))
        except Exception as exc:  # noqa: BLE001 - surface, don't kill the UI
            log(f"Job crashed: {exc!r}")
            self.q.put(_Done(False))

    # ------------------------------------------------------------------ poll

    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, _Done):
                    self._finish(item.ok)
                elif isinstance(item, _UpdateCheckDone):
                    self._on_update_check(item.check)
                elif isinstance(item, _UpdateApplied):
                    self._on_update_applied(item)
                else:
                    self._append(str(item))
        except queue.Empty:
            pass
        self.root.after(self.POLL_MS, self._poll)

    def _finish(self, ok: bool) -> None:
        self._set_running(False)
        if self._cancelled:
            self.status.configure(text="Stopped by Cancel. Boost is back on Home.")
        elif ok:
            self.status.configure(text="Job finished -- all parts done.")
        else:
            self.status.configure(text="Job finished with skips/failures -- see log.")
        self._append("===== END =====")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    "AutoBoost",
                    "A job is still running. Stop and exit?\n\n"
                    "Exiting kills the run immediately and may leave Boost "
                    "mid-part. Prefer Cancel, which finishes the current part "
                    "first."):
                return
            self._cancel()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
