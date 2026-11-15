"""국내 패션 산업지(RSS 없음) — 첫 화면·뉴스 사이트맵에서 기사 주소를 얻고, 기사 페이지의 메타(제목·설명·게시일)만 읽는다.

- 어패럴뉴스: 첫 화면의 /news/news_view/?idx= 링크, 게시일은 기사 페이지 JSON-LD datePublished
  (해외 IP를 막아 GitHub 예약 실행에선 쓰지 않음 — APPAREL 참고)
- 패션엔: 첫 화면의 /board/read_new.php?table=&number= 링크, 게시일은 DC.date.issued 메타(날짜만)
- 패션비즈: 첫 화면이 자바스크립트로 그려져 링크가 없음 → robots.txt가 알려 주는 뉴스 사이트맵(server-sitemap.xml)
- 아이즈매거진(v2, 2026-11-15): 뉴스 사이트맵(sitemap-news.xml.gz, 최근 이틀치)에 주소·제목·게시 시각이 다 있다.
  스타일 잡지라 사이트맵 제목으로 feeds.style_keep을 먼저 걸고, 통과한 글만 기사 페이지에서 og:description을 읽는다.
모든 곳 robots.txt에 Claude 수집기 차단 없음(2026-11-13 확인). 막힌 경로는 robots.txt를 읽어 건너뛴다.
"""
from __future__ import annotations

import html
import re
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .. import http
from .base import Context, raw
from .feeds import style_keep

KST = timezone(timedelta(hours=9))
MAX_AGE = timedelta(hours=48)
PER_SITE = 6


@dataclass(frozen=True)
class Site:
    key: str
    outlet: str
    base: str                                    # https://호스트
    list_url: str
    links: Callable[[str], list[tuple[str, str]]] | None = None   # 목록 문서 → [(기사 번호, https 주소)] 최신 먼저
    date: Callable[[str, dict], datetime | None] | None = None    # (기사 HTML, 목록에서 얻은 값) → 게시 시각
    kind: str = "home"            # "news_sitemap"이면 fetch_news_sitemap (제목·게시 시각을 사이트맵에서)
    style: bool = False           # 스타일 매체 — 사이트맵 제목으로 style_keep을 건다


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)   # 시간대 없으면 KST (날짜만 있으면 그날 0시)


def _newest_first(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen, out = set(), []
    for num, url in sorted(pairs, key=lambda p: -int(p[0])):
        if num not in seen:
            seen.add(num)
            out.append((num, url))
    return out


def _apparel_links(doc: str) -> list[tuple[str, str]]:
    return _newest_first([(m, f"https://www.apparelnews.co.kr/news/news_view/?idx={m}")
                          for m in re.findall(r"/news/news_view/\?cat=[^&\"']*&(?:amp;)?idx=(\d+)", doc)])


# 패션엔 게시판 중 업계 소식이 실리는 곳만 (2026-11-13 메뉴 확인). 스타패션(1023)·인스타일(1024)·스트리트·필름·라이프는
# 셀럽 착장·화보라 첫 화면 자리를 다 차지하므로 읽지 않는다.
FASHIONN_TABLES = {"1002": "패션칼럼", "1003": "Hot & New", "1004": "핫이슈", "1006": "패션뉴스", "1010": "피플앤토크",
                   "1025": "World Fashion"}


def _fashionn_links(doc: str) -> list[tuple[str, str]]:
    return _newest_first([(n, f"https://www.fashionn.com/board/read_new.php?table={t}&number={n}")
                          for t, n in re.findall(r"/board/read_new\.php\?table=(\d+)&(?:amp;)?number=(\d+)", doc)
                          if t in FASHIONN_TABLES])


SITEMAP_URL = re.compile(r"<url>(.*?)</url>", re.S)


def _fashionbiz_links(doc: str) -> list[tuple[str, str]]:
    out = []
    for block in SITEMAP_URL.findall(doc):
        m = re.search(r"<loc>https://fashionbiz\.co\.kr/article/(\d+)</loc>", block)
        if m:
            out.append((m.group(1), f"https://fashionbiz.co.kr/article/{m.group(1)}"))
    return _newest_first(out)


def _fashionbiz_sitemap_dates(doc: str) -> dict[str, str]:
    dates = {}
    for block in SITEMAP_URL.findall(doc):
        m = re.search(r"/article/(\d+)</loc>", block)
        d = re.search(r"<news:publication_date>([^<]+)</news:publication_date>", block)
        if m and d:
            dates[m.group(1)] = d.group(1)
    return dates


def _date_jsonld(page: str, _: dict) -> datetime | None:
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page)
    return _date(m.group(1)) if m else None


def _date_dc(page: str, _: dict) -> datetime | None:
    m = re.search(r'<meta\s+name="DC\.date\.issued"\s+content="([^"]+)"', page, re.I)
    return _date(m.group(1)) if m else None


def _date_listed(page: str, listed: dict) -> datetime | None:
    return _date(listed.get("date"))


# 어패럴뉴스는 해외 IP 접속을 받지 않는다(2026-11-14 GitHub 서버(미국)에서 연결 시간 초과, 국내 PC에선 열림).
# 그래서 예약 실행 목록(SITES)에서는 빼고 구글 뉴스로 제목만 받는다(gnews.py). 국내에서 돌릴 때를 위해 정의는 남긴다.
APPAREL = Site("apparel", "어패럴뉴스", "https://www.apparelnews.co.kr", "https://www.apparelnews.co.kr/",
               _apparel_links, _date_jsonld)

# robots.txt는 /admin/만 막는다(2026-11-15 확인) → 원문 읽음.
EYESMAG = Site("eyesmag", "아이즈매거진", "https://www.eyesmag.com", "https://www.eyesmag.com/sitemap/sitemap-news.xml.gz",
               kind="news_sitemap", style=True)

SITES: tuple[Site, ...] = (
    Site("fashionn", "패션엔", "https://www.fashionn.com", "https://www.fashionn.com/", _fashionn_links, _date_dc),
    Site("fashionbiz", "패션비즈", "https://fashionbiz.co.kr", "https://fashionbiz.co.kr/server-sitemap.xml",
         _fashionbiz_links, _date_listed),
    EYESMAG,
)

CDATA = re.compile(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", re.S)


def _xml_text(s: str) -> str:
    m = CDATA.match(s or "")
    return html.unescape(m.group(1) if m else (s or "")).replace(" ", " ").strip()


def news_sitemap_entries(doc: str) -> list[dict]:
    """뉴스 사이트맵 → [{num, url, title, published}] 게시 시각 최신 먼저. 번호·제목·시각 중 하나라도 없으면 뺀다."""
    out = []
    for block in SITEMAP_URL.findall(doc):
        loc = re.search(r"<loc>\s*([^<]+?)\s*</loc>", block)
        title = re.search(r"<news:title>(.*?)</news:title>", block, re.S)
        pub = re.search(r"<news:publication_date>([^<]+)</news:publication_date>", block)
        url = _xml_text(loc.group(1)) if loc else ""
        num = re.search(r"/posts/(\d+)(?:/|$)", url)
        when = _date(pub.group(1)) if pub else None
        if not (num and url.startswith("https://") and title and _xml_text(title.group(1)) and when):
            continue
        out.append({"num": num.group(1), "url": url, "title": _xml_text(title.group(1)), "published": when})
    return sorted(out, key=lambda e: e["published"], reverse=True)


def meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta\s[^>]*property="{re.escape(prop)}"[^>]*content="([^"]*)"', page, re.I) or \
        re.search(rf'<meta\s[^>]*content="([^"]*)"[^>]*property="{re.escape(prop)}"', page, re.I)
    return html.unescape(m.group(1)).replace("\xa0", " ").strip() if m else ""


def robots_for(site: Site) -> urllib.robotparser.RobotFileParser:
    """robots.txt가 없으면(404) 막힌 곳 없음. 그 밖의 이유로 못 받으면(403·5xx·시간 초과) 보수적으로 그 회차는 읽지 않는다."""
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(http.get_text(f"{site.base}/robots.txt", timeout=15).splitlines())
    except http.FetchError as e:
        if "HTTP 404" not in str(e):
            raise http.FetchError(f"robots.txt를 못 받아 건너뜀: {e}") from e
        rp.parse([])
    return rp


def fetch_news_sitemap(site: Site, ctx: Context, rp, doc: str) -> list[dict]:
    """사이트맵 방식(아이즈매거진). before_filter = 48시간 안·아직 모르는 글 전체 수(상한에서 멈추지 않고 셈).
    필터에 걸린 글·아는 글·48시간 넘은 글은 기사 페이지를 받지 않는다. 기사 하나의 실패는 그 글만 건너뛴다."""
    out, seen = [], 0
    for e in news_sitemap_entries(doc):
        id_ = f"kr-{site.key}-{e['num']}"
        if id_ in ctx.known_ids or ctx.now - e["published"] > MAX_AGE:
            continue
        seen += 1
        if len(out) >= PER_SITE or (site.style and not style_keep(e["title"], "")):
            continue
        if not e["url"].isascii() or not rp.can_fetch(http.UA, e["url"]):
            ctx.notes.setdefault("krsite_articles", []).append(f"{site.key} {e['num']}: 읽지 않는 주소"[:150])
            continue
        try:
            page = http.get_text(e["url"], timeout=25)
        except Exception as err:            # 기사 하나(삭제·시간 초과·주소 오류)가 매체 전체를 날리지 않게
            ctx.notes.setdefault("krsite_articles", []).append(f"{site.key} {e['num']}: {err}"[:150])
            continue
        out.append(raw(id=id_, title=e["title"], url=e["url"], source="krsite", region="kr", outlet=site.outlet,
                       excerpt=meta(page, "og:description"), published_at=e["published"].isoformat()))
    ctx.notes.setdefault("krsite_before_filter", {})[site.outlet] = seen
    return out


def fetch_site(site: Site, ctx: Context) -> list[dict]:
    rp = robots_for(site)
    if not rp.can_fetch(http.UA, site.list_url):
        raise http.FetchError(f"robots.txt가 목록 주소를 막음: {site.list_url}")
    doc = http.get_text(site.list_url, timeout=25)
    if site.kind == "news_sitemap":
        return fetch_news_sitemap(site, ctx, rp, doc)
    listed_dates = _fashionbiz_sitemap_dates(doc) if site.key == "fashionbiz" else {}
    out = []
    for num, url in site.links(doc):
        if len(out) >= PER_SITE:
            break
        id_ = f"kr-{site.key}-{num}"
        if id_ in ctx.known_ids or not rp.can_fetch(http.UA, url):
            continue                        # 이미 아는 글은 기사 페이지를 받지 않는다
        listed = _date(listed_dates.get(num))
        if listed and ctx.now - listed > MAX_AGE:
            break                           # 번호 순(최신 먼저)이라 뒤는 더 오래됨
        try:
            page = http.get_text(url, timeout=25)
        except http.FetchError as e:        # 기사 하나(삭제·시간 초과)가 매체 전체를 날리지 않게
            ctx.notes.setdefault("krsite_articles", []).append(f"{site.key} {num}: {e}"[:150])
            continue
        pub = site.date(page, {"date": listed_dates.get(num)})
        title = meta(page, "og:title")
        if pub and ctx.now - pub > MAX_AGE:
            break
        if not pub or not title:
            continue                        # 게시일·제목을 못 찾은 글은 버림
        out.append(raw(id=id_, title=title, url=url, source="krsite", region="kr", outlet=site.outlet,
                       excerpt=meta(page, "og:description"), published_at=pub.isoformat()))
    return out


def fetch(ctx: Context) -> list[dict]:
    """매체 하나가 실패해도 나머지는 계속. 모두 실패하면 예외. 매체별 건수·오류를 남긴다."""
    out, errors = [], []
    outlets = ctx.notes.setdefault("outlets", {}).setdefault("krsite", {})
    for site in SITES:
        try:
            got = fetch_site(site, ctx)
            out += got
            outlets[site.outlet] = {"fetched": len(got)}
            if site.style:              # 스타일 매체만 "N건 중 M건" (패션엔·패션비즈는 그대로)
                outlets[site.outlet]["before_filter"] = ctx.notes.get("krsite_before_filter", {}).pop(site.outlet, 0)
        except Exception as e:
            errors.append(f"{site.key}: {e}")
            outlets[site.outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and len(errors) == len(SITES):
        raise http.FetchError("; ".join(errors))
    ctx.notes.pop("krsite_before_filter", None)
    ctx.notes["krsite"] = errors + ctx.notes.pop("krsite_articles", [])   # 기사 단위 실패도 상태 메모로
    return out
