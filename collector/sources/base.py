"""소스 공통 타입과 도우미."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime

MAX_PER_SOURCE = 30


@dataclass
class Context:
    now: datetime
    known_ids: set[str] = field(default_factory=set)
    notes: dict = field(default_factory=dict)   # 소스가 남기는 부분 실패 메모·매체별 건수 (status에 기록)


def raw(*, id, title, url, source, region, outlet, brief=False, score=None, comments=None, excerpt="",
        published_at=None) -> dict:
    return {
        "id": id,
        "title": clean(title),
        "url": url,
        "source": source,
        "region": region,
        "outlet": outlet,
        "brief": brief,
        "score": score,
        "comments": comments,
        "excerpt": clean(excerpt)[:300],
        "body": clean(excerpt)[:3000],   # 상세 작성 때 원문을 못 읽으면 쓰는 대체 본문 (저장하지 않음)
        "published_at": published_at,
    }


TAG = re.compile(r"<[^>]+>")


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", s or ""))).strip()
