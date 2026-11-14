"""S3 — 국내 산업지 직접 읽기(krsite)·구글 뉴스(gnews). 픽스처는 2026-11-13 받은 실제 응답을 줄인 것."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

from collector import http
from collector.sources import gnews, krsite
from collector.sources.base import Context

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
SITE = {s.key: s for s in krsite.SITES + (krsite.APPAREL,)}
ALL_SITES = krsite.SITES + (krsite.APPAREL,)   # 국내에서 돌릴 때 — 예약 실행 목록엔 어패럴뉴스가 없다


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def fake_web(calls, robots=None, article=None):
    """주소별로 픽스처를 돌려준다. 요청한 주소를 calls에 남긴다."""
    def get_text(url, **kw):
        calls.append(url)
        host = {"apparelnews": "apparel", "fashionn": "fashionn", "fashionbiz": "fashionbiz"}
        key = next((v for k, v in host.items() if k in url), None)
        if url.endswith("/robots.txt"):
            return robots or read(f"kr_{key}_robots.txt")
        if url == "https://www.apparelnews.co.kr/":
            return read("kr_apparel_list.html")
        if url == "https://www.fashionn.com/":
            return read("kr_fashionn_list.html")
        if url.endswith("server-sitemap.xml"):
            return read("kr_fashionbiz_sitemap.xml")
        if key:
            return article or read(f"kr_{key}_article.html")
        raise AssertionError(url)
    return get_text


def test_link_extraction_per_site():
    """S3 AC① — 매체별 링크 추출, cat= 값만 다른 같은 기사는 하나로(같은 id)."""
    ap = krsite._apparel_links(read("kr_apparel_list.html"))
    assert ap[0] == ("228961", "https://www.apparelnews.co.kr/news/news_view/?idx=228961")
    assert len({n for n, _ in ap}) == len(ap)
    dup = '<a href="/news/news_view/?cat=CAT100&idx=5"><a href="/news/news_view/?cat=CAT210&amp;idx=5">'
    assert krsite._apparel_links(dup) == [("5", "https://www.apparelnews.co.kr/news/news_view/?idx=5")]
    fn = krsite._fashionn_links(read("kr_fashionn_list.html"))
    assert fn[0] == ("63146", "https://www.fashionn.com/board/read_new.php?table=1003&number=63146")
    assert not any("table=1023" in u or "table=1024" in u for _, u in fn)      # 스타패션·인스타일 제외
    assert "number=63145" not in " ".join(u for _, u in fn)                     # 1024 게시판 글
    fb = krsite._fashionbiz_links(read("kr_fashionbiz_sitemap.xml"))
    assert fb[0] == ("231062", "https://fashionbiz.co.kr/article/231062")
    assert all(u.startswith("https://") for _, u in ap + fn + fb)


def test_meta_and_dates_from_real_pages():
    """S3 AC① — og 메타·게시일. 날짜만 있으면 KST 0시."""
    ap = read("kr_apparel_article.html")
    assert krsite.meta(ap, "og:title").startswith("美 아동복 전문")
    assert krsite._date_jsonld(ap, {}) == datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
    assert krsite._date_dc(read("kr_fashionn_article.html"), {}) == datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
    dates = krsite._fashionbiz_sitemap_dates(read("kr_fashionbiz_sitemap.xml"))
    assert krsite._date(dates["231061"]) == datetime(2026, 11, 12, 8, 40, tzinfo=timezone.utc)


def test_fetch_site_ids_https_and_og_url_ignored(monkeypatch):
    """S3 AC① — 저장 주소는 받아 온 https 주소(어패럴뉴스 og:url이 http여도), id는 kr-매체-번호."""
    page = read("kr_apparel_article.html")
    assert 'og:url' in page
    page = page.replace("https://www.apparelnews.co.kr/news/news_view/", "http://www.apparelnews.co.kr/news/news_view/")
    monkeypatch.setattr(http, "get_text", fake_web([], article=page))
    got = krsite.fetch_site(SITE["apparel"], Context(now=NOW))
    assert len(got) == krsite.PER_SITE
    r = got[0]
    assert r["id"] == "kr-apparel-228961" and r["url"] == "https://www.apparelnews.co.kr/news/news_view/?idx=228961"
    assert (r["source"], r["region"], r["outlet"], r["brief"]) == ("krsite", "kr", "어패럴뉴스", False)
    assert r["excerpt"].startswith("미국의 대표적인 아동복")


def test_known_ids_skip_page_fetch(monkeypatch):
    """S3 AC① — 이미 아는 글은 기사 페이지를 받지 않는다."""
    calls = []
    monkeypatch.setattr(http, "get_text", fake_web(calls))
    known = {f"kr-apparel-{n}" for n, _ in krsite._apparel_links(read("kr_apparel_list.html"))}
    assert krsite.fetch_site(SITE["apparel"], Context(now=NOW, known_ids=known)) == []
    assert not any("news_view" in c for c in calls)


def test_old_or_undated_articles_dropped(monkeypatch):
    """S3 AC① — 48시간 넘은 글·게시일 못 찾은 글 버림."""
    calls = []
    monkeypatch.setattr(http, "get_text", fake_web(calls))
    later = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)          # 기사(11-12 KST) 기준 48시간 넘음
    assert krsite.fetch_site(SITE["apparel"], Context(now=later)) == []
    assert sum("news_view" in c for c in calls) == 1                     # 첫 글이 오래됐으면 더 받지 않음
    undated = read("kr_apparel_article.html").replace("datePublished", "dateX")
    monkeypatch.setattr(http, "get_text", fake_web([], article=undated))
    assert krsite.fetch_site(SITE["apparel"], Context(now=NOW)) == []


def test_fashionbiz_uses_sitemap_date_and_skips_old_without_fetching(monkeypatch):
    calls = []
    monkeypatch.setattr(http, "get_text", fake_web(calls))
    got = krsite.fetch_site(SITE["fashionbiz"], Context(now=NOW))
    assert [r["id"] for r in got][:2] == ["kr-fashionbiz-231062", "kr-fashionbiz-231061"]
    assert got[0]["title"].startswith("스킨케어 뷰티 브랜드 바바코")
    calls.clear()
    assert krsite.fetch_site(SITE["fashionbiz"], Context(now=datetime(2026, 11, 20, tzinfo=timezone.utc))) == []
    assert not any("/article/" in c for c in calls)


def test_robots_disallowed_paths_not_fetched(monkeypatch):
    """S3 AC① — robots.txt에서 막힌 경로는 읽지 않는다."""
    calls = []
    monkeypatch.setattr(http, "get_text", fake_web(calls, robots="User-agent: *\nDisallow: /news/news_view/\n"))
    assert krsite.fetch_site(SITE["apparel"], Context(now=NOW)) == []
    assert not any("news_view" in c for c in calls)
    monkeypatch.setattr(http, "get_text", fake_web([], robots="User-agent: *\nDisallow: /\n"))
    with pytest.raises(http.FetchError, match="robots"):
        krsite.fetch_site(SITE["apparel"], Context(now=NOW))


def test_krsite_failure_isolated_and_outlets(monkeypatch):
    web = fake_web([])

    def flaky(url, **kw):
        if url == "https://www.fashionn.com/":
            raise http.FetchError("HTTP 503")
        return web(url, **kw)

    monkeypatch.setattr(http, "get_text", flaky)
    monkeypatch.setattr(krsite, "SITES", ALL_SITES)
    ctx = Context(now=NOW)
    out = krsite.fetch(ctx)
    got = ctx.notes["outlets"]["krsite"]
    assert set(got) == {"어패럴뉴스", "패션엔", "패션비즈"}
    assert got["패션엔"]["fetched"] == 0 and "503" in got["패션엔"]["error"]
    assert {r["outlet"] for r in out} == {"어패럴뉴스", "패션비즈"}


# --- gnews ---

def test_split_title_only_matching_tail():
    """S3 AC② — 꼬리가 <source>와 같을 때만 뗀다."""
    assert gnews.split_title("Burberry - A Turnaround - The Business of Fashion", "The Business of Fashion") == "Burberry - A Turnaround"
    assert gnews.split_title("Burberry - A Turnaround", "The Business of Fashion") == "Burberry - A Turnaround"
    assert gnews.split_title("Title - Vogue", "") == "Title - Vogue"


def test_gnews_fixture_all_brief_and_48h(monkeypatch):
    """S3 AC② — 같은 guid → 같은 id, 48시간 넘은 글 버림, 모두 brief. 회차당 요청 1회(BoF)."""
    calls = []
    bof = gnews.QUERIES[0]
    monkeypatch.setattr(http, "get_text", lambda url, **kw: calls.append(url) or read("gnews_bof.xml"))
    ctx = Context(now=NOW)
    got = gnews.fetch_query(bof, ctx)
    assert len(calls) == 1 and "site:businessoffashion.com" in unquote(calls[0]) and "hl=en-US" in calls[0]
    titles = [r["title"] for r in got]
    assert titles[0] == "In Fashion’s Holiday Campaigns, the Medium Is the Message"
    assert "Tech Mode" not in titles and "paris-fashion-week" not in titles   # 2월·2021년 글
    assert len(got) == bof.per_query
    assert all(r["brief"] is True and r["region"] == "global" and r["outlet"] == "The Business of Fashion"
               and r["source"] == "gnews" and r["id"].startswith("gnews-") and len(r["id"]) == 16 for r in got)
    again = gnews.fetch_query(bof, Context(now=NOW))
    assert [r["id"] for r in again] == [r["id"] for r in got]



# --- S9 리뷰 반영 ---

def test_one_article_failure_keeps_rest_of_site(monkeypatch):
    """기사 하나가 404여도 그 매체의 다른 글은 남고, 실패는 상태 메모로."""
    web = fake_web([])
    first = krsite._apparel_links(read("kr_apparel_list.html"))[0][1]

    def flaky(url, **kw):
        if url == first:
            raise http.FetchError(f"HTTP 404 {url}")
        return web(url, **kw)

    monkeypatch.setattr(http, "get_text", flaky)
    monkeypatch.setattr(krsite, "SITES", ALL_SITES)
    ctx = Context(now=NOW)
    out = krsite.fetch(ctx)
    ap = [r for r in out if r["outlet"] == "어패럴뉴스"]
    assert len(ap) == krsite.PER_SITE and all(r["url"] != first for r in ap)
    assert any("404" in n for n in ctx.notes["krsite"])


@pytest.mark.parametrize("err,blocked", [("HTTP 404 x", False), ("HTTP 403 x", True), ("HTTP 503 x", True),
                                         ("TimeoutError x", True)])
def test_robots_unreachable_is_conservative(monkeypatch, err, blocked):
    """robots.txt가 없으면(404) 허용, 못 받으면(403·5xx·시간 초과) 그 회차는 읽지 않음."""
    web = fake_web([])

    def get_text(url, **kw):
        if url.endswith("/robots.txt"):
            raise http.FetchError(err)
        return web(url, **kw)

    monkeypatch.setattr(http, "get_text", get_text)
    if blocked:
        with pytest.raises(http.FetchError, match="robots.txt를 못 받아"):
            krsite.fetch_site(SITE["apparel"], Context(now=NOW))
    else:
        assert len(krsite.fetch_site(SITE["apparel"], Context(now=NOW))) == krsite.PER_SITE



# --- 프랑스·이탈리아 (구글 뉴스) ---

EU_NOW = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
APPAREL_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
EU_FIX = {"fr.fashionnetwork.com": "gnews_fn_fr.xml", "lesechos.fr": "gnews_lesechos.xml", "it.fashionnetwork.com": "gnews_fn_it.xml",
          "businessoffashion.com": "gnews_bof.xml", "apparelnews.co.kr": "gnews_apparel.xml"}


def test_eu_queries_language_outlet_and_title_tail(monkeypatch):
    calls = []

    def get_text(url, **kw):
        calls.append(url)
        return read(next(v for k, v in EU_FIX.items() if k in unquote(url)))

    monkeypatch.setattr(http, "get_text", get_text)
    ctx = Context(now=EU_NOW)
    got = gnews.fetch(ctx)
    assert len(calls) == len(gnews.QUERIES) == 5
    assert any("hl=fr&gl=FR&ceid=FR:fr" in c for c in calls) and any("hl=it&gl=IT&ceid=IT:it" in c for c in calls)
    by = {}
    for r in got:
        by.setdefault(r["outlet"], []).append(r)
    assert set(ctx.notes["outlets"]["gnews"]) == {q.outlet for q in gnews.QUERIES}
    fr = by["FashionNetwork (FR)"]
    assert fr[0]["title"] == "Frasers : jeu de chaises musicales à la direction"        # ' - FashionNetwork France' 꼬리 뗌
    assert len(fr) <= 4 and len(by.get("Les Echos (FR)", [])) <= 3
    assert all(r["brief"] is True and r["region"] == ("kr" if r["outlet"] == "어패럴뉴스" else "global") for r in got)



def test_apparelnews_via_google_news_is_korean_brief(monkeypatch):
    """어패럴뉴스는 해외 IP를 막아 구글 뉴스(한국어판)로 제목만 — 국내(kr)·원문 미확인, 꼬리 ' - 어패럴뉴스' 뗌."""
    calls = []
    q = next(q for q in gnews.QUERIES if q.outlet == "어패럴뉴스")
    monkeypatch.setattr(http, "get_text", lambda url, **kw: calls.append(url) or read("gnews_apparel.xml"))
    got = gnews.fetch_query(q, Context(now=APPAREL_NOW))
    assert "hl=ko&gl=KR&ceid=KR:ko" in calls[0] and "site:apparelnews.co.kr" in unquote(calls[0])
    assert got and all(r["region"] == "kr" and r["brief"] is True and r["outlet"] == "어패럴뉴스" for r in got)
    assert not any(r["title"].endswith("어패럴뉴스") for r in got)
    assert krsite.APPAREL not in krsite.SITES                                   # 예약 실행에선 직접 읽지 않음
