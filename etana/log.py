"""Structured logging: one stderr handler, `level=... event=... key=value` lines.

Stdlib only — a kv formatter gives grep-able, observable runs (rows in, rows
after dedupe, intervals expected vs present) without a structlog dependency.
Usage:  log.info("rows_read", extra=kv(feed="meterflow", rows=2873))
"""

import logging
import sys


def kv(**pairs) -> dict:
    """Attach key=value pairs to a log record via `extra`."""
    return {"kv": pairs}


class KVFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            f"level={record.levelname.lower()}",
            f"event={record.getMessage()}",
        ]
        for key, value in (getattr(record, "kv", None) or {}).items():
            text = str(value)
            parts.append(f"{key}={text!r}" if " " in text else f"{key}={text}")
        return " ".join(parts)


class _LiveStderrHandler(logging.StreamHandler):
    """Resolves sys.stderr at emit time, not at setup time — under pytest the
    stream is swapped per test, and a handler bound to a finished test's
    stream would raise on every later emit."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):  # StreamHandler.__init__ assigns; ignore it
        pass


def setup(level: int = logging.INFO) -> None:
    handler = _LiveStderrHandler()
    handler.setFormatter(KVFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
