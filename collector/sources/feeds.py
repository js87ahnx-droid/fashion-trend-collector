"""공개 RSS/Atom 피드 — 해외 패션 업계지·잡지, 국내 섬유·패션 신문·잡지. 모두 source="feed", 매체는 outlet으로 구분."""
from __future__ import annotations

import hashlib
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
    style: bool = False           # 스타일 잡지 — 비즈니스 단어 필터 대상 (S2)


FEEDS: tuple[Feed, ...] = ()   # S2에서 채움


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


def parse(xml_text: str, tz: timezone = timezone.utc) -> list[dict]:
    """RSS 2.0·Atom 공통: [{title, link, guid, published, summary, comments}]"""
    root = ET.fromstring(xml_text)
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
    if not url.startswith("https://") or not entry["title"].strip():
        return None
    key = entry["guid"] or url
    pub = entry["published"]
    return raw(id=f"{feed.key}-" + hashlib.sha1(key.encode()).hexdigest()[:10], title=entry["title"], url=url,
               source="feed", region=feed.region, outlet=feed.outlet, brief=not feed.fetchable,
               excerpt=entry["summary"], published_at=pub.isoformat() if pub else None)


def fetch_feed(feed: Feed, ctx: Context) -> list[dict]:
    out = []
    for e in parse(http.get_text(feed.url, timeout=25), feed.tz):
        if not e["published"] or ctx.now - e["published"] > feed.max_age:
            continue
        r = to_raw(e, feed)
        if r and r["id"] not in ctx.known_ids:
            out.append(r)
        if len(out) >= feed.max_items:
            break
    return out


def fetch(ctx: Context) -> list[dict]:
    """피드 하나가 실패해도 나머지는 계속. 모두 실패하면 예외. 매체별 건수·오류를 ctx.notes["outlets"]에 남긴다."""
    out, errors = [], []
    outlets = ctx.notes.setdefault("outlets", {}).setdefault("feeds", {})
    for feed in FEEDS:
        try:
            got = fetch_feed(feed, ctx)
            out += got
            outlets[feed.outlet] = {"fetched": len(got)}
        except Exception as e:
            errors.append(f"{feed.key}: {e}")
            outlets[feed.outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and not out and len(errors) == len(FEEDS):
        raise http.FetchError("; ".join(errors))
    ctx.notes["feeds"] = errors
    return out
