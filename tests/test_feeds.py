from datetime import datetime, timezone
from pathlib import Path

import pytest

from collector import http
from collector.sources import feeds
from collector.sources.base import Context

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
WEEK = feeds.timedelta(days=7)
SAMPLE = feeds.Feed("sample", "https://x/feed", "Sample", "global", 3, WEEK)


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_rss_parse_and_age_and_https(monkeypatch):
    monkeypatch.setattr(http, "get_text", lambda url, **kw: read("feed_substack.xml"))
    out = feeds.fetch_feed(SAMPLE, Context(now=NOW))
    assert len(out) == 1                                   # 7일 넘은 글·http 링크 제외
    r = out[0]
    assert r["source"] == "feed" and r["id"].startswith("sample-") and len(r["id"]) == 17
    assert (r["outlet"], r["region"], r["brief"]) == ("Sample", "global", False)
    assert r["title"] == "How AI product teams write evals before PRDs"
    assert r["excerpt"] == "Episode summary placeholder & more"


def test_atom_entries_parsed():
    e = feeds.parse(read("feed_ph.xml"))[0]
    r = feeds.to_raw(e, SAMPLE)
    assert r["url"] == "https://www.producthunt.com/products/sample-ai" and r["id"].startswith("sample-")


def test_unfetchable_outlet_marks_brief():
    e = feeds.parse(read("feed_ph.xml"))[0]
    vogue = feeds.Feed("vogue", "u", "Vogue", "global", 3, WEEK, fetchable=False)
    assert feeds.to_raw(e, vogue)["brief"] is True


def two_feeds():
    return (feeds.Feed("a", "https://a/feed", "A매체", "kr", 3, WEEK),
            feeds.Feed("b", "https://b/feed", "B매체", "global", 3, WEEK))


def test_one_feed_failure_does_not_stop_others_and_outlets_reported(monkeypatch):
    """S1 AC⑩ — 0건 매체·실패 매체까지 매체별 줄이 남는다."""
    def fake(url, **kw):
        if "//a/" in url:
            raise http.FetchError("HTTP 503 a")
        return read("feed_substack.xml")

    monkeypatch.setattr(feeds, "FEEDS", two_feeds())
    monkeypatch.setattr(http, "get_text", fake)
    ctx = Context(now=NOW)
    out = feeds.fetch(ctx)
    assert [r["outlet"] for r in out] == ["B매체"]
    assert "a:" in ctx.notes["feeds"][0]
    got = ctx.notes["outlets"]["feeds"]
    assert set(got) == {"A매체", "B매체"}
    assert got["A매체"]["fetched"] == 0 and "503" in got["A매체"]["error"]
    assert got["B매체"] == {"fetched": 1}


def test_all_feeds_failing_raises(monkeypatch):
    monkeypatch.setattr(feeds, "FEEDS", two_feeds())
    monkeypatch.setattr(http, "get_text", lambda url, **kw: (_ for _ in ()).throw(http.FetchError("down")))
    with pytest.raises(http.FetchError):
        feeds.fetch(Context(now=NOW))
