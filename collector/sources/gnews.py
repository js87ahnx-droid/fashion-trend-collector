"""BoF — 자체 RSS가 막혀(403) 구글 뉴스 RSS 검색으로 새 글을 찾는다.
(Vogue Business는 구글 뉴스 결과에 제목이 비어 와서 쓰지 않고, Vogue RSS의 Business 분류로 받는다 — feeds.py)

기사 페이지와 구글 링크는 열지 않는다(BoF robots.txt가 다수 수집기를 막고 AI 학습을 거부, 유료 기사).
제목·매체·날짜만 쓰는 brief 글이다. 링크는 구글 뉴스 주소 그대로(누르면 원문으로 넘어감).
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import timedelta
from urllib.parse import quote

from .. import http
from .base import Context, raw
from .feeds import _date

MAX_AGE = timedelta(hours=48)
PER_QUERY = 6
QUERIES = (   # (질의, 화면에 쓸 기본 매체 이름)
    ("site:businessoffashion.com when:2d", "The Business of Fashion"),
)


def search_url(q: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(q)}&hl=en-US&gl=US&ceid=US:en"


def split_title(title: str, source: str) -> str:
    """'기사 제목 - 매체명'에서 꼬리가 <source> 값과 같을 때만 뗀다. 제목 안의 다른 ' - '는 그대로."""
    title = (title or "").strip()
    tail = f" - {source.strip()}" if source else ""
    return title[: -len(tail)].strip() if tail and title.endswith(tail) else title


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.iter("item"):
        source = (e.findtext("source") or "").strip()
        out.append({
            "title": split_title(e.findtext("title", ""), source),
            "link": (e.findtext("link") or "").strip(),
            "guid": (e.findtext("guid") or "").strip(),
            "published": _date(e.findtext("pubDate")),
            "source": source,
        })
    return out


def fetch_query(q: str, default_outlet: str, ctx: Context) -> list[dict]:
    out = []
    for e in parse(http.get_text(search_url(q), timeout=25)):
        if not e["published"] or ctx.now - e["published"] > MAX_AGE:
            continue
        if not e["link"].startswith("https://") or not e["title"]:
            continue
        id_ = "gnews-" + hashlib.sha1((e["guid"] or e["link"]).encode()).hexdigest()[:10]
        if id_ in ctx.known_ids:
            continue
        out.append(raw(id=id_, title=e["title"], url=e["link"], source="gnews", region="global",
                       outlet=e["source"] or default_outlet, brief=True, published_at=e["published"].isoformat()))
        if len(out) >= PER_QUERY:
            break
    return out


def fetch(ctx: Context) -> list[dict]:
    out, errors = [], []
    outlets = ctx.notes.setdefault("outlets", {}).setdefault("gnews", {})
    for q, outlet in QUERIES:
        try:
            got = fetch_query(q, outlet, ctx)
            out += got
            outlets[outlet] = {"fetched": len(got)}
        except Exception as e:
            errors.append(f"{outlet}: {e}")
            outlets[outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and len(errors) == len(QUERIES):
        raise http.FetchError("; ".join(errors))
    ctx.notes["gnews"] = errors
    return out
