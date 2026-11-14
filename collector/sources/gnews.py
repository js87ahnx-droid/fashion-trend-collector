"""구글 뉴스 RSS 검색으로 새 글만 찾는 매체 — 자체 RSS가 막혔거나 수집기를 막은 곳.

- BoF: RSS 403, robots.txt가 다수 수집기를 막고 AI 학습 거부, 유료 기사
- FashionNetwork 프랑스·이탈리아: RSS 403, robots.txt가 Claude 수집기 일부를 막음 (2026-11-14 확인)
- Les Echos(럭셔리): RSS 403, 유료 기사
(Vogue Business는 구글 뉴스 결과에 제목이 비어 와서 쓰지 않고, Vogue RSS의 Business 분류로 받는다 — feeds.py)

기사 페이지와 구글 링크는 열지 않는다. 제목·매체·날짜만 쓰는 brief 글이다. 링크는 구글 뉴스 주소 그대로.
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from .. import http
from .base import Context, raw
from .feeds import _date

MAX_AGE = timedelta(hours=48)


@dataclass(frozen=True)
class Query:
    q: str
    outlet: str        # 화면에 쓸 매체 이름 (구글 뉴스 <source> 값과 달라도 이 이름으로)
    hl: str = "en-US"  # 구글 뉴스 언어·나라
    gl: str = "US"
    per_query: int = 6


QUERIES: tuple[Query, ...] = (
    Query("site:businessoffashion.com when:2d", "The Business of Fashion"),
    Query("site:fr.fashionnetwork.com when:2d", "FashionNetwork (FR)", "fr", "FR", 4),
    Query("site:lesechos.fr luxe when:2d", "Les Echos (FR)", "fr", "FR", 3),
    Query("site:it.fashionnetwork.com when:2d", "FashionNetwork (IT)", "it", "IT", 4),
)


def search_url(query: Query) -> str:
    lang = query.hl.split("-")[0]
    return f"https://news.google.com/rss/search?q={quote(query.q)}&hl={query.hl}&gl={query.gl}&ceid={query.gl}:{lang}"


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


def fetch_query(query: Query, ctx: Context) -> list[dict]:
    out = []
    for e in parse(http.get_text(search_url(query), timeout=25)):
        if not e["published"] or ctx.now - e["published"] > MAX_AGE:
            continue
        if not e["link"].startswith("https://") or not e["title"]:
            continue
        id_ = "gnews-" + hashlib.sha1((e["guid"] or e["link"]).encode()).hexdigest()[:10]
        if id_ in ctx.known_ids:
            continue
        out.append(raw(id=id_, title=e["title"], url=e["link"], source="gnews", region="global",
                       outlet=query.outlet, brief=True, published_at=e["published"].isoformat()))
        if len(out) >= query.per_query:
            break
    return out


def fetch(ctx: Context) -> list[dict]:
    out, errors = [], []
    outlets = ctx.notes.setdefault("outlets", {}).setdefault("gnews", {})
    for query in QUERIES:
        try:
            got = fetch_query(query, ctx)
            out += got
            outlets[query.outlet] = {"fetched": len(got)}
        except Exception as e:
            errors.append(f"{query.outlet}: {e}")
            outlets[query.outlet] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:200]}
    if errors and len(errors) == len(QUERIES):
        raise http.FetchError("; ".join(errors))
    ctx.notes["gnews"] = errors
    return out
