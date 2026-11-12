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
    assert len(out) == 2                                   # 7일 넘은 글 제외, http 링크는 https로 바꿔 남김
    assert all(o["url"].startswith("https://") for o in out)
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
    assert {r["outlet"] for r in out} == {"B매체"}
    assert "a:" in ctx.notes["feeds"][0]
    got = ctx.notes["outlets"]["feeds"]
    assert set(got) == {"A매체", "B매체"}
    assert got["A매체"]["fetched"] == 0 and "503" in got["A매체"]["error"]
    assert got["B매체"] == {"fetched": 2}


def test_all_feeds_failing_raises(monkeypatch):
    monkeypatch.setattr(feeds, "FEEDS", two_feeds())
    monkeypatch.setattr(http, "get_text", lambda url, **kw: (_ for _ in ()).throw(http.FetchError("down")))
    with pytest.raises(http.FetchError):
        feeds.fetch(Context(now=NOW))


# --- S2: 실제 피드 9곳 (2026-11-12 받은 응답에서 글 3건씩 남긴 픽스처) ---

FIX_NOW = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)
BY_KEY = {f.key: f for f in feeds.FEEDS}
FIRST = {   # 매체: (첫 글 제목 앞부분, 주소 앞부분, region)
    "wwd": ("Kathryn Hahn Breaks Up", "https://wwd.com/footwear-news/", "global"),
    "glossy": ("Brands are banking on live shopping", "https://www.glossy.co/beauty/", "global"),
    "fashionista": ("Must Read: PVH Corp.", "https://fashionista.com/2026/11/pvh-corp-layoffs", "global"),
    "vogue": ("40+ Affordable Jewelry Brands", "https://www.vogue.com/article/", "global"),
    "hypebeast": ("Subtitle Launches Godzilla", "https://hypebeast.com/2026/11/", "global"),
    "ktnews": ("외국인 방한 이천만 시대", "https://www.ktnews.com/news/articleView.html?idxno=150147", "kr"),
    "voguekr": ("켄달 제너, 헤일리 비버가", "https://www.vogue.co.kr/2026/11/12/", "kr"),
    "wkorea": ("추워지면 나도 모르게", "https://www.wkorea.com/2026/11/12/", "kr"),
    "bazaarkr": ("투명 팁부터 촛불 네일까지", "https://www.harpersbazaar.co.kr/article/1913036", "kr"),
}


def test_nine_feeds_configured():
    assert set(BY_KEY) == set(FIRST)
    assert {f.outlet for f in feeds.FEEDS if not f.fetchable} == {"Vogue", "보그코리아", "W코리아"}
    assert {f.outlet for f in feeds.FEEDS if f.style} == {"Vogue", "Hypebeast", "보그코리아", "W코리아", "바자코리아"}
    assert all(f.url.startswith("https://") and f.max_age == feeds.timedelta(hours=48) for f in feeds.FEEDS)
    assert BY_KEY["ktnews"].max_items == 8


@pytest.mark.parametrize("key", sorted(FIRST))
def test_fixture_parses_per_outlet(key):
    """S2 AC① — 매체마다 실제 응답으로 제목·주소·날짜·region·outlet 확인 (필터 없이 to_raw만)."""
    feed = BY_KEY[key]
    entries = feeds.parse(read(f"rss_{key}.xml"), feed.tz)
    assert len(entries) == 3 and all(e["published"] for e in entries)
    r = feeds.to_raw(entries[0], feed)
    title, url, region = FIRST[key]
    assert r["title"].startswith(title) and r["url"].startswith(url)
    assert (r["region"], r["outlet"], r["source"]) == (region, feed.outlet, "feed")
    assert r["brief"] is (not feed.fetchable)                  # S2 AC②-2
    assert r["published_at"].startswith("2026-11-12")


def test_ktnews_naive_date_is_kst_and_http_link_upgraded():
    """S2 AC②-4 — http 링크는 https로, 시간대 없는 날짜는 KST."""
    feed = BY_KEY["ktnews"]
    e = feeds.parse(read("rss_ktnews.xml"), feed.tz)[0]
    assert e["link"].startswith("http://")
    assert e["published"] == datetime(2026, 11, 12, 12, 33, 46, tzinfo=timezone.utc)
    assert feeds.to_raw(e, feed)["url"].startswith("https://www.ktnews.com/")


def test_vogue_korea_undeclared_media_prefix_still_parses():
    """보그코리아 피드는 media: 접두어를 선언하지 않아 그대로는 XML 오류 — 임시 선언으로 읽는다."""
    text = read("rss_voguekr.xml")
    with pytest.raises(feeds.ET.ParseError):
        feeds.ET.fromstring(text)
    assert len(feeds.parse(text)) == 3


def test_age_window_and_missing_date(monkeypatch):
    """S2 AC② — 날짜 없는 글·48시간 넘은 글 버림."""
    xml = """<rss><channel>
<item><title>Revenue up</title><link>https://x.com/1</link><pubDate>Thu, 12 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>No date sales</title><link>https://x.com/2</link></item>
<item><title>Old sales</title><link>https://x.com/3</link><pubDate>Mon, 09 Nov 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""
    monkeypatch.setattr(http, "get_text", lambda url, **kw: xml)
    out = feeds.fetch_feed(BY_KEY["wwd"], Context(now=FIX_NOW))
    assert [r["url"] for r in out] == ["https://x.com/1"]


@pytest.mark.parametrize("title,keep", [
    ("Burberry revenue rises in third quarter", True),
    ("Nike to close 30 stores in Europe", True),
    ("Kering names new CEO", True),
    ("젠틀몬스터, 성수 매장 확장", True),
    ("삼성물산 패션, 아미 인수했다", True),
    ("국내 아웃도어 유통업계 재편", True),
    ("올겨울 대표적인 룩 5가지", False),
    ("Restore your wardrobe this winter", False),
    ("유통기한 지난 화장품 버리는 법", False),
    ("인수분해처럼 쉬운 레이어드", False),
    ("Storefront-style living room ideas", False),
])
def test_business_word_filter_edges(title, keep):
    """S2 AC③ — 다른 낱말 안에 든 경우는 걸리지 않는다."""
    assert feeds.is_business(title, "") is keep


def test_style_filter_applies_before_cap_and_only_to_style(monkeypatch):
    """S2 AC③ — 스타일 매체만 필터, 필터 뒤에 매체당 최대 건수. 필터 전 건수를 남긴다."""
    items = "".join(
        f"<item><title>{t}</title><link>https://x.com/{i}</link><pubDate>Thu, 12 Nov 2026 10:0{i}:00 +0000</pubDate></item>"
        for i, t in enumerate(["Winter boots", "Fur jackets", "Long boots", "Party looks", "Kering earnings beat", "Gucci retail push"]))
    monkeypatch.setattr(http, "get_text", lambda url, **kw: f"<rss><channel>{items}</channel></rss>")
    style = feeds.Feed("s", "https://s/feed", "스타일", "global", 1, feeds.timedelta(hours=48), style=True)
    plain = feeds.Feed("p", "https://p/feed", "업계지", "global", 5, feeds.timedelta(hours=48))
    counts = {}
    got = feeds.fetch_feed(style, Context(now=FIX_NOW), counts)
    assert [r["title"] for r in got] == ["Kering earnings beat"] and counts["before_filter"] == 6
    assert len(feeds.fetch_feed(plain, Context(now=FIX_NOW))) == 5


def test_outlet_status_has_before_filter_only_for_style(monkeypatch):
    monkeypatch.setattr(http, "get_text", lambda url, **kw: read(f"rss_{next(f.key for f in feeds.FEEDS if f.url == url)}.xml"))
    ctx = Context(now=FIX_NOW)
    feeds.fetch(ctx)
    got = ctx.notes["outlets"]["feeds"]
    assert set(got) == {f.outlet for f in feeds.FEEDS}
    assert "before_filter" in got["Vogue"] and "before_filter" not in got["WWD"]
    assert got["WWD"]["fetched"] == 3 and got["한국섬유신문"]["fetched"] == 3


def test_block_page_reported_as_fetch_error(monkeypatch):
    """한국섬유신문처럼 200 응답에 차단 페이지(HTML)를 주면 XML 오류가 아니라 알아볼 수 있는 오류로."""
    page = '<head><meta charset="utf-8"></head>\n<body><img src="403.jpg"></body>\n'
    monkeypatch.setattr(http, "get_text", lambda url, **kw: page)
    with pytest.raises(http.FetchError, match="RSS가 아닌 응답"):
        feeds.fetch_feed(BY_KEY["ktnews"], Context(now=FIX_NOW))


def test_user_agent_has_no_word_collector():
    assert "collector" not in http.UA.lower() and "mozilla" not in http.UA.lower()
