"""The targeted NOPASSWD sudo grant + the cache clear it enables.

Cited from mjolnir's ``cachectl.py`` (mjolnir-cli): detection by ``sudo -n -l``
(never prompts), argv passed as separate elements so sudo matches the rule,
install via temp file + ``visudo -cf`` + one ``sudo install``. Never full sudo.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Optional

from spark.window.ctx import Ctx

SYSCTL = "/usr/sbin/sysctl"
NVIDIA_SMI = "/usr/bin/nvidia-smi"
DROP_CACHES_ARGV = ["sudo", "-n", SYSCTL, "-w", "vm.drop_caches=3"]
TEE_ARGV = ["sudo", "-n", "tee", "/proc/sys/vm/drop_caches"]
SUDOERS_DROPIN = "/etc/sudoers.d/spark-lobes"

_SYSCTL_RE = re.compile(r"sysctl.*vm\.drop_caches")
_TEE_RE = re.compile(r"tee /proc/sys/vm/drop_caches")
_ALL_RE = re.compile(r"NOPASSWD:\s*ALL\b")
_WINDOW_RE = re.compile(r"nvidia-smi.*-(lgc|rgc)")


def listing(ctx: Ctx) -> Optional[str]:
    """``sudo -n -l`` stdout, or None when sudo is missing/locked."""
    rc, out, _ = ctx.proc(["sudo", "-n", "-l"], None)
    return out if rc == 0 else None


def detect_drop_caches(text: Optional[str]) -> Optional[str]:
    """Return the rule kind ('sysctl' | 'tee' | 'all') or None."""
    if not text:
        return None
    if _SYSCTL_RE.search(text):
        return "sysctl"
    if _TEE_RE.search(text):
        return "tee"
    if _ALL_RE.search(text):
        return "all"
    return None


def detect_window(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    if _WINDOW_RE.search(text):
        return "nvidia-smi"
    if _ALL_RE.search(text):
        return "all"
    return None


def drop_caches(ctx: Ctx) -> dict:
    """Clear the page cache. Returns the contract's result object + ``exit``."""
    rule = detect_drop_caches(listing(ctx))
    if rule is None:
        return {
            "exit": 2,
            "ok": False,
            "reason": "not_granted",
            "cleared": False,
            "rule": None,
            "hint": "run 'spark grant-permissions'",
        }
    ctx.proc(["sync"], None)
    if rule == "tee":
        rc, _, err = ctx.proc(TEE_ARGV, "3")
    else:
        rc, _, err = ctx.proc(DROP_CACHES_ARGV, None)
    if rc != 0:
        return {
            "exit": 2,
            "ok": False,
            "reason": "failed",
            "cleared": False,
            "rule": rule,
            "stderr": err.strip(),
        }
    return {"exit": 0, "ok": True, "reason": None, "cleared": True, "rule": rule}


def rules_for(user: str, supported: bool) -> list[str]:
    """One sudoers rule per command; never full sudo."""
    rules = [f"{user} ALL=(ALL) NOPASSWD: {SYSCTL} -w vm.drop_caches=3"]
    if supported:
        rules.append(f"{user} ALL=(ALL) NOPASSWD: {NVIDIA_SMI} -lgc *")
        rules.append(f"{user} ALL=(ALL) NOPASSWD: {NVIDIA_SMI} -rgc")
    return rules


def check(ctx: Ctx, supported: bool) -> dict:
    text = listing(ctx)
    dc = detect_drop_caches(text)
    win = detect_window(text) if supported else None
    return {
        "ok": True,
        "reason": None,
        "drop_caches": {"granted": dc is not None, "rule": dc, "source": "sudo -n -l"},
        "window": {"granted": win is not None, "rule": win, "supported": supported},
    }


def install(ctx: Ctx, supported: bool, assume_yes: bool, confirm=None) -> dict:
    """Install missing rules. ``confirm`` is a callable(rules)->bool (the only prompt)."""
    rep = check(ctx, supported)
    need_dc = not rep["drop_caches"]["granted"]
    need_win = supported and not rep["window"]["granted"]
    if not need_dc and not need_win:
        rep["installed"] = False
        rep["exit"] = 0
        return rep
    rules = rules_for(ctx.user(), supported)
    if not need_dc:
        rules = rules[1:]
    elif not need_win:
        rules = rules[:1]
    rep["rules"] = rules
    if not assume_yes and not (confirm and confirm(rules)):
        rep.update(ok=False, reason="declined", installed=False, exit=1)
        return rep
    fd, tmp = tempfile.mkstemp(prefix="spark-sudoers-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(rules) + "\n")
        rc, out, err = ctx.proc(["visudo", "-cf", tmp], None)
        if rc != 0:
            rep.update(ok=False, reason="failed", installed=False, exit=2)
            rep["stderr"] = (out + err).strip()
            return rep
        rc, out, err = ctx.proc(
            ["sudo", "install", "-m", "0440", "-o", "root", "-g", "root", tmp, SUDOERS_DROPIN],
            None,
        )
        if rc != 0:
            rep.update(ok=False, reason="failed", installed=False, exit=2)
            rep["stderr"] = (out + err).strip()
            return rep
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    rep.update(installed=True, exit=0, path=SUDOERS_DROPIN)
    return rep
