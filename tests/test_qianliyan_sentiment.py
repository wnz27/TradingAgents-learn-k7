import sys
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import requests

from tradingagents.dataflows.vendors.qianliyan import (
    NewsBoundary,
    live_news_boundary,
    read_daily_sentiment_context,
)


def test_daily_market_sentiment_is_disabled_by_default(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("disabled archive integration made a request")

    monkeypatch.setattr(requests, "get", unexpected_request)
    config = {"qianliyan": {"base_url": "https://archive.example"}}
    assert live_news_boundary(config) is None
    assert read_daily_sentiment_context(config, None) is None


def test_daily_market_sentiment_is_a_separate_context_at_the_same_archive_boundary(monkeypatch):
    calls = []
    cutoff = datetime(2026, 11, 24, 22, tzinfo=UTC)
    config = {"qianliyan": {"base_url": "https://archive.example", "daily_sentiment": True}}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "as_of": cutoff.isoformat(),
                "snapshot": "stable-token",
                "series": [
                    {
                        "series": {"series_id": "us_vix", "unit": "index_points"},
                        "latest": {"trading_date": "2026-11-24", "value": "18.1200"},
                        "change": "-0.18",
                        "release_check": None,
                    },
                    {
                        "series": {"series_id": "cn_fear_greed", "unit": "score"},
                        "latest": {"trading_date": "2026-11-23", "value": "39"},
                        "change": None,
                        "release_check": {"target_date": "2026-11-24", "status": "WAITING_RELEASE"},
                    },
                    {
                        "series": {"series_id": "cn_short_term_sentiment", "unit": "score"},
                        "latest": None,
                        "change": None,
                        "release_check": None,
                    },
                ],
            }

    def get(url, *, params, timeout):
        calls.append((url, params))
        return Response()

    monkeypatch.setattr(requests, "get", get)
    boundary = live_news_boundary(config)
    assert boundary == NewsBoundary(cutoff, "stable-token")
    result = read_daily_sentiment_context(config, boundary)

    assert calls == [
        ("https://archive.example/api/query/v1/news", {"limit": 1}),
        (
            "https://archive.example/api/query/v1/sentiment",
            {
                "as_of": cutoff.isoformat(),
                "snapshot": "stable-token",
            },
        ),
    ]
    assert "Archived Daily Market Sentiment" in result
    assert "us_vix" in result and "18.1200 index_points" in result and "-0.18" in result
    assert "cn_fear_greed" in result and "WAITING_RELEASE" in result
    assert "cn_short_term_sentiment" in result and "No confirmed observation" in result


@pytest.mark.skipif(sys.version_info < (3, 13), reason="Qianliyan requires Python 3.13")
def test_local_daily_sentiment_reads_the_version_at_the_news_boundary(tmp_path):
    pytest.importorskip("qianliyan.query")
    from qianliyan.collection.contracts import DailyBatch, DailyRecord, to_utc_us
    from qianliyan.sentiment.archive import record, seed_series
    from qianliyan.storage.sqlite import SQLiteArchive

    now = datetime(2026, 11, 24, 18, tzinfo=UTC)
    archive = SQLiteArchive(tmp_path / "daily.sqlite", clock=lambda: to_utc_us(now))
    archive.migrate()

    with archive.write() as writer:
        seed_series(writer.connection)

    def append(value, mode):
        batch = DailyBatch(records=(DailyRecord("us_vix", date(2026, 11, 24), Decimal(value)),))
        with archive.write() as writer:
            run_id = writer.connection.execute(
                """
                INSERT INTO collection_run (task_id, provider_id, mode, started_at, outcome)
                VALUES ('daily_vix', 'fred', ?, 0, 'SUCCEEDED')
                """,
                (mode,),
            ).lastrowid
            record(writer.connection, run_id, "fred", batch, mode)

    config = {"qianliyan": {"archive_path": str(archive.path), "daily_sentiment": True}}
    append("18.1200", "SCHEDULED")
    first = live_news_boundary(config)
    now = datetime(2026, 11, 24, 20, tzinfo=UTC)
    append("18.6500", "REVISION")
    latest = live_news_boundary(config)
    assert "18.1200 index_points" in read_daily_sentiment_context(config, first)
    assert "18.6500 index_points" in read_daily_sentiment_context(config, latest)
