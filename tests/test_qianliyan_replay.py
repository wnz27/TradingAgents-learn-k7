import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import requests

from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.news_replay import StrictNewsReplay
from tradingagents.dataflows.vendors.qianliyan import NewsBoundary, get_news, pin_news_snapshot

CONFIG = {"qianliyan": {"base_url": "https://archive.example"}}


def test_same_date_decision_cutoffs_and_saved_cell_boundaries(tmp_path, monkeypatch):
    from tradingagents.dataflows import news_replay

    def resolve(config, cutoff):
        return NewsBoundary(cutoff, f"snapshot-{cutoff.hour}")

    monkeypatch.setattr(news_replay, "pin_news_snapshot", resolve)
    early = StrictNewsReplay(tmp_path / "early", CONFIG, "09:25", "America/New_York")
    late = StrictNewsReplay(tmp_path / "late", CONFIG, "16:05", "America/New_York")
    with ThreadPoolExecutor(2) as pool:
        early_cell = pool.submit(early.boundary, "NVDA", "2026-11-24")
        late_cell = pool.submit(late.boundary, "NVDA", "2026-11-24")
    assert early_cell.result() == NewsBoundary(
        datetime(2026, 11, 24, 14, 25, tzinfo=UTC), "snapshot-14"
    )
    assert late_cell.result() == NewsBoundary(
        datetime(2026, 11, 24, 21, 5, tzinfo=UTC), "snapshot-21"
    )

    resumed = StrictNewsReplay(tmp_path / "early", CONFIG, "09:25", "America/New_York")
    assert resumed.boundary("NVDA", "2026-11-24") == early_cell.result()
    assert resumed.saved_boundary("NVDA", "2026-11-24") == early_cell.result()
    with pytest.raises(ValueError, match="does not match"):
        StrictNewsReplay(tmp_path / "early", CONFIG, "16:05", "America/New_York")


def test_resume_rejects_a_replaced_archive_boundary(tmp_path, monkeypatch):
    from tradingagents.dataflows import news_replay

    snapshot = "original"
    monkeypatch.setattr(
        news_replay, "pin_news_snapshot", lambda config, cutoff: NewsBoundary(cutoff, snapshot)
    )
    replay = StrictNewsReplay(tmp_path, CONFIG, "09:25", "UTC")
    replay.boundary("NVDA", "2026-11-24")

    snapshot = "different-archive"
    with pytest.raises(VendorUnavailableError, match="no longer matches"):
        replay.boundary("NVDA", "2026-11-24")


def test_unavailable_historical_coverage_does_not_store_a_cell(tmp_path, monkeypatch):
    from tradingagents.dataflows import news_replay

    def unavailable(config, cutoff):
        raise VendorUnavailableError("Historical Qianliyan archive coverage is unavailable")

    monkeypatch.setattr(news_replay, "pin_news_snapshot", unavailable)
    replay = StrictNewsReplay(tmp_path, CONFIG, "09:25", "UTC")
    with pytest.raises(VendorUnavailableError, match="Historical"):
        replay.boundary("NVDA", "2026-11-24")
    assert replay.saved_boundary("NVDA", "2026-11-24") is None


@pytest.mark.skipif(sys.version_info < (3, 13), reason="Qianliyan requires Python 3.13")
@pytest.mark.parametrize("transport", ["local", "http"])
def test_real_archive_keeps_earlier_cutoff_before_late_backfill(tmp_path, monkeypatch, transport):
    pytest.importorskip("qianliyan.query")
    from qianliyan.collection.contracts import (
        NewsAssociation,
        NewsBatch,
        NewsRecord,
        TargetKind,
        to_utc_us,
    )
    from qianliyan.news.archive import record
    from qianliyan.query import ArchiveQueries
    from qianliyan.storage.sqlite import SQLiteArchive

    def point(hour):
        return datetime(2026, 11, 24, hour, tzinfo=UTC)

    now = point(14)
    archive = SQLiteArchive(tmp_path / "archive.sqlite", clock=lambda: to_utc_us(now))
    archive.migrate()
    association = NewsAssociation(TargetKind.INSTRUMENT, "US:NVDA", "US", "NVDA")

    def append(title, upstream_id):
        batch = NewsBatch(
            records=(
                NewsRecord(
                    title=title,
                    original_url=f"https://example.com/{upstream_id}",
                    publisher="Original Press",
                    published_at=point(13),
                    upstream_id=upstream_id,
                    associations=(association,),
                ),
            )
        )
        with archive.write() as writer:
            run_id = writer.connection.execute(
                """
                INSERT INTO collection_run (task_id, provider_id, mode, started_at, outcome)
                VALUES ('market_news', 'yahoo', 'BACKFILL', 0, 'SUCCEEDED')
            """
            ).lastrowid
            record(
                writer.connection,
                run_id,
                "yahoo",
                batch,
                {(association.kind, association.key)},
                "BACKFILL",
            )

    if transport == "http":
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from qianliyan.dashboard.http import create_app

        client = TestClient(create_app(ArchiveQueries(archive)))
        monkeypatch.setattr(
            requests, "get", lambda url, *, params, timeout: client.get(url, params=params)
        )
        config = CONFIG
    else:
        config = {"qianliyan": {"archive_path": str(archive.path)}}

    append("Morning article", "morning")
    now = point(15)
    early = pin_news_snapshot(config, now)
    now = point(20)
    append("Late historical backfill", "late")
    now = point(22)
    late = pin_news_snapshot(config, point(21))

    assert early == pin_news_snapshot(config, point(15))
    with run_config({**config, "_qianliyan_boundary": early}):
        earliest = get_news("NVDA", "2026-11-24", "2026-11-24")
    with run_config({**config, "_qianliyan_boundary": late}):
        afternoon = get_news("NVDA", "2026-11-24", "2026-11-24")
    assert "Morning article" in earliest and "Late historical backfill" not in earliest
    assert "Morning article" in afternoon and "Late historical backfill" in afternoon
