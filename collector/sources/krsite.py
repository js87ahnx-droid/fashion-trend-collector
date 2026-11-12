"""국내 패션 산업지(RSS 없음) — 첫 화면·뉴스 사이트맵에서 기사 주소를 얻고, 기사 페이지의 메타(제목·설명·게시일)만 읽는다.

- 어패럴뉴스: 첫 화면의 /news/news_view/?idx= 링크, 게시일은 기사 페이지 JSON-LD datePublished
- 패션엔: 첫 화면의 /board/read_new.php?table=&number= 링크, 게시일은 DC.date.issued 메타(날짜만)
- 패션비즈: 첫 화면이 자바스크립트로 그려져 링크가 없음 → robots.txt가 알려 주는 뉴스 사이트맵(server-sitemap.xml)
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

KST = timezone(timedelta(hours=9))
MAX_AGE = timedelta(hours=48)
PER_SITE = 6


@dataclass(frozen=True)
class Site:
    key: str
    outlet: str
    base: str                                    # https://호스트
    list_url: str
    links: Callable[[str], list[tuple[str, str]]]   # 목록 문서 → [(기사 번호, https 주소)] 최신 먼저
    date: Callable[[str, dict], datetime | None]    # (기사 HTML, 목록에서 얻은 값) → 게시 시각


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


SITES: tuple[Site, ...] = (
    Site("apparel", "어패럴뉴스", "https://www.apparelnews.co.kr", "https://www.apparelnews.co.kr/", _apparel_links, _date_jsonld),
    Site("fashionn", "패션엔", "https://www.fashionn.com", "https://www.fashionn.com/", _fashionn_links, _date_dc),
    Site("fashionbiz", "패션비즈", "https://fashionbiz.co.kr", "https://fashionbiz.co.kr/server-sitemap.xml",
         _fashionbiz_links, _date_listed),
)


def meta(page: str, prop: str) -> str:
    m = re.search(rf'<meta\s[^>]*property="{re.escape(prop)}"[^>]*content="([^"]*)"', page, re.I) or \
        re.search(rf'<meta\s[^>]*content="([^"]*)"[^>]*property="{re.escape(prop)}"', page, re.I)
    return html.unescape(m.group(1)).replace("\xa0", " ").strip() if m else ""


def robots_for(site: Site) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(http.get_text(f"{site.base}/robots.txt", timeout=15).splitlines())
    except http.FetchError:
        rp.parse([])            # robots.txt가 없으면 막힌 곳 없음
    return rp


def fetch_site(site: Site, ctx: Context) -> list[dict]:
    rp = robots_for(site)
    if not rp.can_fetch(http.UA, site.list_url):
        raise http.FetchError(f"robots.txt가 목록 주소를 막음: {site.list_url}")
    doc = http.get_text(site.list_url, timeout=25)
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
        page = http.get_text(url, timeout=25)
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
        except Exception as e:
            errors.append(f"{site.key}: {e}")
            outlets[site.outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and len(errors) == len(SITES):
        raise http.FetchError("; ".join(errors))
    ctx.notes["krsite"] = errors
    return out
