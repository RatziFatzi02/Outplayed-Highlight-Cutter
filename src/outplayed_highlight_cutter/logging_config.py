from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    log_dir = local_app_data / "OutplayedHighlightCutter" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "application.log"
    root = logging.getLogger("outplayed_highlight_cutter")
    if root.handlers:
        return log_file
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    rotating = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    rotating.setFormatter(formatter)
    root.addHandler(stream)
    root.addHandler(rotating)
    return log_file
