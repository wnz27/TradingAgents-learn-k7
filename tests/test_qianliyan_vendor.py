from datetime import UTC, datetime

import pytest
import requests

from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.qianliyan import (
    get_global_news,
    get_news,
    pin_news_snapshot,
    resolve_instrument_key,
    selected_for_tool,
)


@pytest.mark.parametrize(
    ("symbol", "key"),
    [
        ("nvda", "US:NVDA"),
        ("700.HK", "HK:0700.HK"),
        ("600519.SS", "CN:600519.SH"),
        ("BTC", "CRYPTO:BTC"),
        ("BTC-USDT", "CRYPTO:BTC-USDT"),
        ("BTCUSD", "CRYPTO:BTC-USD"),
    ],
)
def test_trading_symbols_preserve_archive_instrument_identity(symbol, key):
    assert resolve_instrument_key(symbol) == key


class Response:
    def __init__(self, **payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_both_news_tools_share_a_boundary_but_keep_their_utc_windows(monkeypatch):
    calls = []
    cutoff = "2026-11-24T15:30:00+00:00"
    cfg = {"qianliyan": {"base_url": "https://archive.example"}, "news_article_limit": 2}

    def get(url, *, params, timeout):
        calls.append((url, params, timeout))
        return Response(
            as_of=cutoff,
            snapshot="stable-token",
            coverage={"status": "LIMITED", "reasons": ["sampled"]},
            items=[
                {
                    "title": "Verified title",
                    "publisher": "Original publisher",
                    "provider_id": "yahoo",
                    "original_url": "https://example.com/story",
                    "published_at": "2026-11-24T13:00:00+00:00",
                    "summary": "Evidence summary",
                    "associations": [{"kind": "TOPIC", "key": "federal_reserve"}],
                }
            ],
        )

    monkeypatch.setattr(requests, "get", get)
    boundary = pin_news_snapshot(cfg)
    with run_config({**cfg, "_qianliyan_boundary": boundary}):
        instrument = get_news("NVDA", "2026-11-23", "2026-11-24")
        macro = get_global_news("2026-11-24", 3, 4)

    assert calls[0][1] == {"limit": 1}
    assert calls[1][1] == {
        "symbol": "US:NVDA",
        "limit": 2,
        "published_start_at": "2026-11-23T00:00:00+00:00",
        "published_end_at": "2026-11-25T00:00:00+00:00",
        "as_of": cutoff,
        "snapshot": "stable-token",
    }
    assert calls[2][1] == {
        "market": "macro",
        "limit": 4,
        "published_start_at": "2026-11-21T00:00:00+00:00",
        "published_end_at": "2026-11-25T00:00:00+00:00",
        "as_of": cutoff,
        "snapshot": "stable-token",
    }
    for output in (instrument, macro):
        assert "Verified title" in output and "Original publisher" in output and "yahoo" in output
        assert "https://example.com/story" in output and "LIMITED" in output and "sampled" in output
    assert "federal_reserve" in macro


def test_cutoff_snapshot_uses_the_dedicated_historical_route(monkeypatch):
    cutoff = datetime(2026, 11, 24, 13, tzinfo=UTC)

    def get(url, *, params, timeout):
        assert url == "https://archive.example/api/query/v1/cutoff-snapshot"
        assert params == {"cutoff_at": cutoff.isoformat()}
        return Response(as_of=cutoff.isoformat(), snapshot="historical-token")

    monkeypatch.setattr(requests, "get", get)
    boundary = pin_news_snapshot({"qianliyan": {"base_url": "https://archive.example"}}, cutoff)
    assert (boundary.as_of, boundary.snapshot) == (cutoff, "historical-token")


def test_archive_timeout_is_an_adapter_failure(monkeypatch):
    def get(*args, **kwargs):
        raise requests.Timeout("private remote URL")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(VendorUnavailableError, match="Timeout"):
        pin_news_snapshot({"qianliyan": {"base_url": "https://archive.example"}})


def test_only_explicit_news_tool_configuration_selects_the_archive():
    config = {
        "data_vendors": {"news_data": "qianliyan"},
        "tool_vendors": {
            "get_news": "qianliyan,yfinance",
            "get_global_news": "default",
            "get_insider_transactions": "qianliyan",
        },
    }
    assert selected_for_tool(config, "get_news")
    assert not selected_for_tool(config, "get_global_news")
    assert not selected_for_tool(config, "get_insider_transactions")
