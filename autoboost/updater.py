"""Launch-time update check for AutoBoost.

The workstation runs AutoBoost from a git clone, so "a newer version exists"
means "the branch's remote has commits we don't". The check fetches the
current branch and counts how far behind HEAD is; applying the update is a
fast-forward merge of what was just fetched.

Deliberately best-effort: ANY problem -- git missing, no network, proxy
weirdness, odd repo state -- reports as status "failed" and the caller lets
the user proceed with the version they have. An update check must never
block the tool.

Everything here is stdlib-only and headless (no tkinter), so it is testable
without a display and reusable by an installer.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# The repo root the running package lives in (…/AutoBoost).
REPO_DIR = Path(__file__).resolve().parents[1]

_TIMEOUT = 15  # seconds per local git command; network commands get 2x

# Keep git from flashing a console window when launched from pyw (no console).
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass
class UpdateCheck:
    status: str        # "update" | "current" | "failed"
    detail: str        # human-readable, shown in the log pane
    behind: int = 0    # commits behind the remote (status "update" only)


def _git(*args: str, repo_dir: Path = REPO_DIR,
         timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True,
                          text=True, timeout=timeout, creationflags=_NO_WINDOW)


def _err(p: subprocess.CompletedProcess, fallback: str) -> str:
    lines = (p.stderr.strip() or p.stdout.strip()).splitlines()
    return lines[-1] if lines else fallback


def current_branch(repo_dir: Path = REPO_DIR) -> str:
    p = _git("rev-parse", "--abbrev-ref", "HEAD", repo_dir=repo_dir)
    branch = p.stdout.strip()
    if p.returncode != 0 or not branch or branch == "HEAD":
        raise RuntimeError(_err(p, "cannot determine the current branch"))
    return branch


def check_for_update(repo_dir: Path = REPO_DIR) -> UpdateCheck:
    """Fetch this branch's remote and report whether HEAD is behind it."""
    try:
        branch = current_branch(repo_dir)
        p = _git("fetch", "origin", branch, repo_dir=repo_dir,
                 timeout=_TIMEOUT * 2)
        if p.returncode != 0:
            raise RuntimeError(_err(p, "git fetch failed"))
        p = _git("rev-list", "--count", f"HEAD..origin/{branch}",
                 repo_dir=repo_dir)
        if p.returncode != 0:
            raise RuntimeError(_err(p, "git rev-list failed"))
        behind = int(p.stdout.strip())
        if behind:
            return UpdateCheck("update",
                               f"{behind} update(s) behind origin/{branch}",
                               behind)
        return UpdateCheck("current", f"up to date with origin/{branch}")
    except Exception as exc:  # noqa: BLE001 - the check must never raise
        return UpdateCheck("failed", str(exc) or exc.__class__.__name__)


def apply_update(repo_dir: Path = REPO_DIR) -> tuple[bool, str]:
    """Fast-forward HEAD to what check_for_update fetched. Returns (ok, msg).

    --ff-only so a locally diverged/dirty clone errors out cleanly instead of
    creating a merge -- that error is reported, never raised, and the user
    keeps working on the version they have.
    """
    try:
        branch = current_branch(repo_dir)
        p = _git("merge", "--ff-only", f"origin/{branch}", repo_dir=repo_dir,
                 timeout=_TIMEOUT * 2)
        if p.returncode != 0:
            return False, f"update failed: {_err(p, 'git merge error')}"
        return True, (f"updated to {installed_version(repo_dir)} -- "
                      f"restart AutoBoost to run it")
    except Exception as exc:  # noqa: BLE001
        return False, f"update failed: {exc}"


def installed_version(repo_dir: Path = REPO_DIR) -> str:
    """The version now ON DISK (after an update the running import is stale)."""
    try:
        text = (repo_dir / "autoboost" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else "a newer version"
    except OSError:
        return "a newer version"
