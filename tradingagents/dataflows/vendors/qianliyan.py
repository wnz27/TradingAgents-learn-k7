"""Read a pinned Qianliyan news archive through HTTP or its local Python package."""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import (
    VendorError,
    VendorNotConfiguredError,
    VendorUnavailableError,
)
from tradingagents.dataflows.symbols import crypto_base

NEWS_TOOLS = ("get_news", "get_global_news")
BOUNDARY_KEY = "_qianliyan_boundary"


@dataclass(frozen=True)
class NewsBoundary:
    as_of: datetime | None
    snapshot: str | None
    error: str | None = None


def resolve_instrument_key(symbol: str, mappings: Mapping[str, str] | None = None) -> str:
    """Translate research ticker syntax without applying another vendor's proxies."""
    ticker = symbol.strip().upper()
    if mappings and ticker in mappings:
        return mappings[ticker]
    if re.fullmatch(r"(?:US|CN|HK|CRYPTO):[A-Z0-9][A-Z0-9.-]*", ticker):
        return ticker
    if match := re.fullmatch(r"([0-9]{1,5})\.HK", ticker):
        return f"HK:{int(match[1]):04d}.HK"
    if match := re.fullmatch(r"([0-9]{6})\.(SH|SS|SZ|BJ)", ticker):
        code, exchange = match.groups()
        return f"CN:{code}.{'SH' if exchange == 'SS' else exchange}"
    if ticker in ("BTC", "ETH"):
        return f"CRYPTO:{ticker}"
    if match := re.fullmatch(r"([A-Z0-9]+?)-?(USDT|USDC|USD)", ticker):
        base, quote = match.groups()
        if base == crypto_base(ticker):
            return f"CRYPTO:{base}-{quote}"
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*", ticker):
        return f"US:{ticker}"
    raise VendorNotConfiguredError(f"No Qianliyan instrument mapping for {symbol!r}")


def selected_for_tool(config: Mapping, method: str) -> bool:
    """Qianliyan is selected only by an explicit news tool vendor chain."""
    chain = config.get("tool_vendors", {}).get(method, "")
    return method in NEWS_TOOLS and "qianliyan" in (vendor.strip() for vendor in chain.split(","))


def _field(row, name):
    return row[name] if isinstance(row, Mapping) else getattr(row, name)


def _boundary(row) -> NewsBoundary:
    try:
        as_of = _field(row, "as_of")
        if isinstance(as_of, str):
            as_of = datetime.fromisoformat(as_of)
        snapshot = _field(row, "snapshot")
        if as_of.utcoffset() is None or not isinstance(snapshot, str) or not snapshot:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise VendorUnavailableError("Qianliyan returned an invalid archive boundary") from None
    return NewsBoundary(as_of.astimezone(UTC), snapshot)


def _read_archive(config, *, endpoint="news", cutoff_at=None, published_window=None, **criteria):
    settings = config.get("qianliyan", {})
    base_url = settings.get("base_url")
    archive_path = settings.get("archive_path")
    if bool(base_url) == bool(archive_path):
        raise VendorNotConfiguredError(
            "Configure one Qianliyan base_url or archive_path for archive queries"
        )
    if base_url:
        if cutoff_at is not None:
            endpoint = "cutoff-snapshot"
            params = {"cutoff_at": cutoff_at.isoformat()}
        else:
            params = {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in criteria.items()
            }
            if published_window is not None:
                start, end = published_window
                params.update(
                    published_start_at=start.isoformat(), published_end_at=end.isoformat()
                )
        try:
            response = requests.get(
                f"{base_url.rstrip('/')}/api/query/v1/{endpoint}", params=params, timeout=5
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as error:
            if error.response is not None and error.response.status_code == 409:
                raise VendorUnavailableError(
                    "Historical Qianliyan archive coverage is unavailable"
                ) from None
            raise VendorUnavailableError("Qianliyan archive rejected the request") from None
        except (requests.RequestException, ValueError) as error:
            raise VendorUnavailableError(
                f"Qianliyan archive request failed ({type(error).__name__})"
            ) from None

    if sys.version_info < (3, 13):
        raise VendorNotConfiguredError("Local Qianliyan queries require Python 3.13 or newer")
    try:
        from qianliyan.collection.contracts import TimeWindow
        from qianliyan.query import ArchiveQueries, QueryInputError
        from qianliyan.storage.sqlite import SQLiteArchive
    except ImportError:
        raise VendorNotConfiguredError(
            "Install Qianliyan or use its HTTP query interface"
        ) from None
    queries = ArchiveQueries(SQLiteArchive(archive_path))
    try:
        if cutoff_at is not None:
            result = queries.resolve_cutoff_snapshot(cutoff_at)
            return {"as_of": cutoff_at, "snapshot": result} if isinstance(result, str) else result
        if endpoint == "sentiment":
            return queries.query_market_sentiment(**criteria)
        if published_window is not None:
            criteria["published_window"] = TimeWindow(*published_window)
        return queries.query_news(**criteria)
    except QueryInputError as error:
        raise VendorUnavailableError(f"Qianliyan archive query unavailable: {error}") from None
    except sqlite3.Error as error:
        raise VendorUnavailableError(
            f"Qianliyan archive could not be read ({type(error).__name__})"
        ) from None


def pin_news_snapshot(config: Mapping, cutoff_at: datetime | None = None) -> NewsBoundary:
    if cutoff_at is not None:
        if cutoff_at.utcoffset() is None:
            raise ValueError("The historical news cutoff must include a time zone")
        cutoff_at = cutoff_at.astimezone(UTC)
    boundary = _boundary(_read_archive(config, cutoff_at=cutoff_at, limit=1))
    if cutoff_at is not None and boundary.as_of != cutoff_at:
        raise VendorUnavailableError("Qianliyan returned a different historical cutoff")
    return boundary


def _daily_sentiment_enabled(config: Mapping) -> bool:
    return config.get("qianliyan", {}).get("daily_sentiment") is True


def live_news_boundary(config: Mapping) -> NewsBoundary | None:
    if not _daily_sentiment_enabled(config) and not any(
        selected_for_tool(config, tool) for tool in NEWS_TOOLS
    ):
        return None
    try:
        return pin_news_snapshot(config)
    except VendorError as error:
        # Let the normal vendor chain handle an unavailable live archive.
        return NewsBoundary(None, None, str(error))


def read_daily_sentiment_context(config: Mapping, boundary: NewsBoundary | None) -> str | None:
    """Render opted-in daily observations as a separate, read-only context."""
    if not _daily_sentiment_enabled(config):
        return None
    heading = "## Qianliyan Archived Daily Market Sentiment"
    if boundary is None or boundary.error or boundary.snapshot is None or boundary.as_of is None:
        return f"{heading}\nArchive snapshot unavailable."
    try:
        result = _read_archive(
            config, endpoint="sentiment", as_of=boundary.as_of, snapshot=boundary.snapshot
        )
        if _boundary(result) != boundary:
            raise VendorUnavailableError("Qianliyan did not preserve the analysis archive boundary")
        lines = [heading, f"Analysis cutoff: {boundary.as_of.isoformat()}"]
        for card in _field(result, "series"):
            series = _field(card, "series")
            point = _field(card, "latest")
            text = "No confirmed observation"
            if point is not None:
                text = (
                    f"{_field(point, 'value')} {_field(series, 'unit')} "
                    f"on {_field(point, 'trading_date')}"
                )
                change = _field(card, "change")
                if change is not None:
                    text += f" (change {change})"
            check = _field(card, "release_check")
            if check is not None:
                text += f" | {_field(check, 'target_date')}: {_field(check, 'status')}"
            lines.append(f"- {_field(series, 'series_id')}: {text}")
        return "\n".join(lines)
    except (VendorError, AttributeError, KeyError, TypeError, ValueError):
        return f"{heading}\nDaily sentiment unavailable for this archive snapshot."


def _render(page, title, start, end, *, macro: bool) -> str:
    items = _field(page, "items")
    coverage = _field(page, "coverage")
    status = _field(coverage, "status") if coverage else "UNVERIFIED"
    reasons = _field(coverage, "reasons") if coverage else ()
    lines = [
        f"## {title}",
        f"UTC publication dates: {start.date()} through {end.date()}",
        f"Analysis cutoff: {_field(page, 'as_of')}",
        f"Archive coverage: {status}",
    ]
    if reasons:
        lines.append("; ".join(reasons))
    if not items:
        lines.append("No matching archived news in this publication window.")
    if macro and items:
        topics = sorted(
            {
                _field(association, "key")
                for item in items
                for association in _field(item, "associations")
                if _field(association, "kind") == "TOPIC"
            }
        )
        if topics:
            lines.append(f"Matched topics: {', '.join(topics)}")
    for item in items:
        lines += [
            f"\n### {_field(item, 'title')}",
            f"Published: {_field(item, 'published_at')} | Publisher: {_field(item, 'publisher')}",
            f"Provider: {_field(item, 'provider_id')} | Source: {_field(item, 'original_url')}",
            _field(item, "summary"),
        ]
    return "\n".join(lines)


def _news(config, start, end, title, *, macro=False, **criteria):
    boundary = config.get(BOUNDARY_KEY) or pin_news_snapshot(config)
    if boundary.error:
        raise VendorUnavailableError(boundary.error)
    page = _read_archive(
        config,
        published_window=(start, end + timedelta(days=1)),
        as_of=boundary.as_of,
        snapshot=boundary.snapshot,
        **criteria,
    )
    if _boundary(page) != boundary:
        raise VendorUnavailableError("Qianliyan did not preserve the analysis archive boundary")
    try:
        return _render(page, title, start, end, macro=macro)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise VendorUnavailableError("Qianliyan returned an invalid news page") from None


def _day(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    config = get_config()
    symbol = resolve_instrument_key(ticker, config.get("qianliyan", {}).get("symbols"))
    return _news(
        config,
        _day(start_date),
        _day(end_date),
        f"Market news for {ticker}",
        symbol=symbol,
        limit=config["news_article_limit"],
    )


def get_global_news(
    as_of_date: str, look_back_days: int | None = None, limit: int | None = None
) -> str:
    config = get_config()
    days = config["global_news_lookback_days"] if look_back_days is None else look_back_days
    limit = config["global_news_article_limit"] if limit is None else limit
    end = _day(as_of_date)
    return _news(
        config,
        end - timedelta(days=days),
        end,
        "Global Market News",
        macro=True,
        market="macro",
        limit=limit,
    )
