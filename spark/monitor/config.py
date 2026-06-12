"""Monitor configuration: thresholds, webhook target, intervals.

Zero-dependency: config is JSON at ``$XDG_CONFIG_HOME/dgx-spark/monitor.json``
(default ``~/.config/dgx-spark/monitor.json``). ``tomllib`` is read-only in the
stdlib, so JSON is used so ``monitor config --init`` can also *write* a scaffold.
``DGX_SPARK_WEBHOOK_URL`` overrides the webhook so secrets needn't live on disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_APP = "dgx-spark"

WEBHOOK_FORMATS = ("generic", "slack", "discord")

# Default catastrophe thresholds — deliberately higher than the descriptive
# ``warnings`` the collectors emit, so the monitor fires on real trouble rather
# than yellow flags. A numeric threshold of ``null`` disables that check; the
# two booleans toggle theirs.
DEFAULT_THRESHOLDS: dict[str, object] = {
    "memory_used_pct": 92.0,
    "swap_used_pct": 75.0,
    "disk_used_pct": 90.0,
    "thermal_max_c": 90.0,
    "gpu_temp_c": 87.0,
    "load_per_core": 4.0,
    "iowait_pct": 25.0,
    "blocked_procs": 8.0,
    "container_unhealthy": True,
    "subsystem_down": True,
}


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))


def default_config_path() -> Path:
    return config_home() / _APP / "monitor.json"


def default_state_path() -> Path:
    return state_home() / _APP / "monitor-state.json"


@dataclass
class Config:
    webhook_url: Optional[str] = None
    webhook_format: str = "generic"  # generic | slack | discord
    interval_seconds: int = 60
    renotify_cycles: int = 30  # re-alert a still-firing condition every N cycles
    timeout_seconds: float = 10.0
    retries: int = 2
    notify_on_start: bool = True  # POST a one-shot "started watching" alert on run
    thresholds: dict = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    source_path: Optional[str] = None  # where it was loaded from (informational)

    def to_dict(self) -> dict:
        return {
            "webhook_url": self.webhook_url,
            "webhook_format": self.webhook_format,
            "interval_seconds": self.interval_seconds,
            "renotify_cycles": self.renotify_cycles,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "notify_on_start": self.notify_on_start,
            "thresholds": self.thresholds,
        }


_FALSE_STRINGS = frozenset({"false", "0", "no", "off", "", "none", "null"})


def _as_bool(value: object) -> bool:
    """Coerce a JSON value to bool, honoring string forms.

    Plain ``bool("false")`` is ``True``, so a mistyped ``"notify_on_start":
    "false"`` would silently leave the flag on. Treat the common string spellings
    of false as false; everything else uses normal truthiness.
    """
    if isinstance(value, str):
        return value.strip().lower() not in _FALSE_STRINGS
    return bool(value)


def _from_dict(data: dict) -> Config:
    cfg = Config()
    cfg.webhook_url = data.get("webhook_url", cfg.webhook_url)
    cfg.webhook_format = data.get("webhook_format", cfg.webhook_format)
    cfg.interval_seconds = int(data.get("interval_seconds", cfg.interval_seconds))
    cfg.renotify_cycles = int(data.get("renotify_cycles", cfg.renotify_cycles))
    cfg.timeout_seconds = float(data.get("timeout_seconds", cfg.timeout_seconds))
    cfg.retries = int(data.get("retries", cfg.retries))
    cfg.notify_on_start = _as_bool(data.get("notify_on_start", cfg.notify_on_start))
    thresholds = dict(DEFAULT_THRESHOLDS)
    if isinstance(data.get("thresholds"), dict):
        thresholds.update(data["thresholds"])
    cfg.thresholds = thresholds
    return cfg


def load(path: Optional[str] = None, *, environ: Optional[dict] = None) -> Config:
    """Load config from ``path`` (or the default), then apply env overrides."""
    env = os.environ if environ is None else environ
    cfg_path = Path(path) if path else default_config_path()
    cfg = Config()
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict):
            try:
                cfg = _from_dict(data)
            except (ValueError, TypeError):
                cfg = Config()
            cfg.source_path = str(cfg_path)
    env_url = env.get("DGX_SPARK_WEBHOOK_URL")
    if env_url:
        cfg.webhook_url = env_url
    return cfg


def validate(cfg: Config) -> list[str]:
    """Return a list of human-readable config problems (empty == valid)."""
    errors: list[str] = []
    if not cfg.webhook_url:
        errors.append("webhook_url is not set (config file or DGX_SPARK_WEBHOOK_URL)")
    elif not str(cfg.webhook_url).startswith(("http://", "https://")):
        errors.append("webhook_url must be an http(s) URL")
    if cfg.webhook_format not in WEBHOOK_FORMATS:
        errors.append("webhook_format must be one of: " + ", ".join(WEBHOOK_FORMATS))
    if cfg.interval_seconds < 1:
        errors.append("interval_seconds must be >= 1")
    return errors


def init_file(path: Optional[str] = None) -> Path:
    """Write a scaffold config (with a placeholder webhook) and return its path."""
    cfg_path = Path(path) if path else default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    scaffold = Config(webhook_url="https://example.com/your-webhook").to_dict()
    cfg_path.write_text(json.dumps(scaffold, indent=2) + "\n", encoding="utf-8")
    return cfg_path
