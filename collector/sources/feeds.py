"""공개 RSS/Atom 피드 — 해외 패션 업계지·잡지, 국내 섬유·패션 신문·잡지. 모두 source="feed", 매체는 outlet으로 구분."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import http
from .base import Context, raw

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Feed:
    key: str          # id 접두어
    url: str
    outlet: str       # 화면에 보이는 매체 이름
    region: str       # kr / global
    max_items: int
    max_age: timedelta
    tz: timezone = timezone.utc   # 날짜에 시간대가 안 적힌 피드의 시간대
    fetchable: bool = True        # False면 원문을 열지 않는 brief 글 (robots.txt가 Claude 수집기를 막은 매체)
    style: bool = False           # 스타일 잡지 — 비즈니스 단어가 있는 글만 남김


H48 = timedelta(hours=48)
# 2026-11-12 직접 확인. fetchable=False: robots.txt가 Claude 수집기(ClaudeBot·Claude-User 등)를 막은 매체.
FEEDS: tuple[Feed, ...] = (
    Feed("wwd", "https://wwd.com/feed/", "WWD", "global", 5, H48),
    Feed("glossy", "https://www.glossy.co/feed/", "Glossy", "global", 5, H48),
    Feed("fashionista", "https://fashionista.com/.rss/full/", "Fashionista", "global", 5, H48),
    Feed("vogue", "https://www.vogue.com/feed/rss", "Vogue", "global", 5, H48, fetchable=False, style=True),
    Feed("hypebeast", "https://hypebeast.com/feed", "Hypebeast", "global", 5, H48, style=True),
    Feed("ktnews", "https://www.ktnews.com/rss/allArticle.xml", "한국섬유신문", "kr", 8, H48, KST),
    Feed("voguekr", "https://www.vogue.co.kr/feed/", "보그코리아", "kr", 5, H48, KST, fetchable=False, style=True),
    Feed("wkorea", "https://www.wkorea.com/feed/", "W코리아", "kr", 5, H48, KST, fetchable=False, style=True),
    Feed("bazaarkr", "https://www.harpersbazaar.co.kr/rss", "바자코리아", "kr", 5, H48, KST, style=True),
)

# 스타일 잡지에서 업계 소식만 고르는 단어. 한국어는 띄어쓰기 경계가 없어 다른 낱말 안에 들지 않게 좁혀 쓴다
# ("대표적인"·"유통기한"·"인수분해"에 걸리지 않게). 영어는 단어 경계.
BIZ_WORDS = re.compile(
    r"매출|실적|영업이익|인수(합병|했|한다|하기로|키로|전)|M&A|대표이사|CEO|매장|유통(사|업계|채널|망)|리테일|입점|상장|투자 유치"
    r"|\b(revenue|sales|profits?|acquisitions?|acquires?|acquired|CEO|retail|retailers?|stores?|IPO|earnings|layoffs?)\b",
    re.I,
)
UNBOUND_PREFIX = re.compile(r"<([A-Za-z][\w.-]*):[A-Za-z]")


def is_business(title: str, summary: str) -> bool:
    return bool(BIZ_WORDS.search(f"{title} {clean_text(summary)[:300]}"))


def clean_text(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _date(s: str | None, tz: timezone = timezone.utc) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _declare_missing_prefixes(xml_text: str) -> str:
    """선언 안 된 접두어(예: 보그코리아 피드의 media:)를 루트 태그에 임시 선언해 읽을 수 있게 한다."""
    used = set(UNBOUND_PREFIX.findall(xml_text)) - {"xml"}
    missing = [p for p in sorted(used) if f"xmlns:{p}=" not in xml_text]
    m = re.search(r"<(rss|feed)\b", xml_text)
    if not missing or not m:
        return xml_text
    decl = "".join(f' xmlns:{p}="urn:undeclared:{p}"' for p in missing)
    return xml_text[: m.end()] + decl + xml_text[m.end():]


def parse(xml_text: str, tz: timezone = timezone.utc) -> list[dict]:
    """RSS 2.0·Atom 공통: [{title, link, guid, published, summary, comments}]"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        root = ET.fromstring(_declare_missing_prefixes(xml_text))
    out = []
    for e in root.iter("item"):
        out.append({
            "title": e.findtext("title", ""),
            "link": (e.findtext("link") or "").strip(),
            "guid": (e.findtext("guid") or "").strip(),
            "published": _date(e.findtext("pubDate"), tz),
            "summary": e.findtext("description") or e.findtext(CONTENT) or "",
            "comments": (e.findtext("comments") or "").strip(),
        })
    for e in root.iter(f"{ATOM}entry"):
        link = next((l.get("href") for l in e.findall(f"{ATOM}link") if l.get("rel", "alternate") == "alternate"), "")
        out.append({
            "title": e.findtext(f"{ATOM}title", ""),
            "link": (link or "").strip(),
            "guid": (e.findtext(f"{ATOM}id") or "").strip(),
            "published": _date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"), tz),
            "summary": e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary") or "",
            "comments": "",
        })
    return out


def to_raw(entry: dict, feed: Feed) -> dict | None:
    url = entry["link"]
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]   # 한국섬유신문 RSS 링크는 http, 같은 주소의 https도 열림(2026-11-12 확인)
    if not url.startswith("https://") or not entry["title"].strip():
        return None
    key = entry["guid"] or url
    pub = entry["published"]
    return raw(id=f"{feed.key}-" + hashlib.sha1(key.encode()).hexdigest()[:10], title=entry["title"], url=url,
               source="feed", region=feed.region, outlet=feed.outlet, brief=not feed.fetchable,
               excerpt=entry["summary"], published_at=pub.isoformat() if pub else None)


def fetch_feed(feed: Feed, ctx: Context, counts: dict | None = None) -> list[dict]:
    """기간 안의 글 → (스타일 잡지면) 비즈니스 단어 필터 → 매체당 최대 건수. counts에 필터 전 건수를 남긴다."""
    out, seen = [], 0
    text = http.get_text(feed.url, timeout=25)
    if not re.search(r"<(rss|feed|rdf:RDF)\b", text[:2000]):
        raise http.FetchError(f"RSS가 아닌 응답(차단 페이지일 수 있음): {text[:80]!r}")
    for e in parse(text, feed.tz):
        if not e["published"] or ctx.now - e["published"] > feed.max_age:
            continue
        r = to_raw(e, feed)
        if not r or r["id"] in ctx.known_ids:
            continue
        seen += 1
        if feed.style and not is_business(e["title"], e["summary"]):
            continue
        if len(out) < feed.max_items:
            out.append(r)
    if counts is not None:
        counts["before_filter"] = seen
    return out


def fetch(ctx: Context) -> list[dict]:
    """피드 하나가 실패해도 나머지는 계속. 모두 실패하면 예외. 매체별 건수·오류를 ctx.notes["outlets"]에 남긴다."""
    out, errors = [], []
    outlets = ctx.notes.setdefault("outlets", {}).setdefault("feeds", {})
    for feed in FEEDS:
        try:
            counts: dict = {}
            got = fetch_feed(feed, ctx, counts)
            out += got
            outlets[feed.outlet] = {"fetched": len(got)} | ({"before_filter": counts["before_filter"]} if feed.style else {})
        except Exception as e:
            errors.append(f"{feed.key}: {e}")
            outlets[feed.outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and not out and len(errors) == len(FEEDS):
        raise http.FetchError("; ".join(errors))
    ctx.notes["feeds"] = errors
    return out
