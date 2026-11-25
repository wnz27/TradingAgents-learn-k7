"""Persist the point-in-time archive boundary of each strict backtest cell."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.qianliyan import NewsBoundary, pin_news_snapshot

REPLAY_DB = "news-replay.sqlite3"


class StrictNewsReplay:
    def __init__(self, run_dir: Path, config: dict, decision_time: str, decision_timezone: str):
        self._time = time.fromisoformat(decision_time)
        if self._time.tzinfo is not None:
            raise ValueError("Decision time must be local to the separately configured time zone")
        self._zone = ZoneInfo(decision_timezone)
        self._config = config
        self._path = run_dir / REPLAY_DB
        if not self._path.exists() and (run_dir / "trading_memory.md").exists():
            raise ValueError("Choose a new run ID for a strict news backtest")

        identity = hashlib.sha256(
            json.dumps(
                {
                    "time": self._time.isoformat(),
                    "timezone": self._zone.key,
                    "archive": config.get("qianliyan", {}),
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()

        run_dir.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_policy (
                    id INTEGER PRIMARY KEY CHECK (id = 1), identity TEXT NOT NULL
                )
            """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_cell (
                    ticker TEXT NOT NULL, trade_date TEXT NOT NULL,
                    cutoff_at TEXT NOT NULL, snapshot TEXT NOT NULL,
                    PRIMARY KEY (ticker, trade_date)
                )
            """
            )
            db.execute("INSERT OR IGNORE INTO replay_policy VALUES (1, ?)", (identity,))
            saved = db.execute("SELECT identity FROM replay_policy WHERE id = 1").fetchone()[0]
            if saved != identity:
                raise ValueError("Strict news policy does not match this backtest run")

    @staticmethod
    def _stored(db: sqlite3.Connection, ticker: str, trade_date: str) -> NewsBoundary | None:
        row = db.execute(
            "SELECT cutoff_at, snapshot FROM replay_cell WHERE ticker = ? AND trade_date = ?",
            (ticker, trade_date),
        ).fetchone()
        return NewsBoundary(datetime.fromisoformat(row[0]), row[1]) if row else None

    def saved_boundary(self, ticker: str, trade_date: str) -> NewsBoundary | None:
        with closing(sqlite3.connect(self._path)) as db:
            return self._stored(db, ticker, trade_date)

    def boundary(self, ticker: str, trade_date: str) -> NewsBoundary:
        local = datetime.combine(date.fromisoformat(trade_date), self._time, self._zone)
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("Decision time falls in an ambiguous or skipped local hour")
        cutoff = local.astimezone(UTC)
        boundary = pin_news_snapshot(self._config, cutoff)
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO replay_cell VALUES (?, ?, ?, ?)",
                (ticker, trade_date, cutoff.isoformat(), boundary.snapshot),
            )
            if self._stored(db, ticker, trade_date) != boundary:
                raise VendorUnavailableError(
                    "Resolved archive boundary no longer matches this backtest cell"
                )
        return boundary
