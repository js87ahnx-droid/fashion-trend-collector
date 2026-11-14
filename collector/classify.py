"""광고 사전 필터 + Claude로 패션 업계 글의 카테고리·헤드라인 분류, 중복·대체 판정."""
from __future__ import annotations

import json
import os
import re

from .store import CATS

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
MAX_TO_CLASSIFY = 25
MAX_STORED = 300

# 광고에만 쓰는 문구. 업계 기사에 흔한 낱말(할인·이벤트·sale·sales)은 넣지 않는다.
AD_WORDS = re.compile(
    r"쿠폰 ?(받기|다운|코드|증정)|할인 ?코드|공동구매|구매 ?링크|"
    r"\b(promo code|discount code|coupon|affiliate link)\b",
    re.I,
)

CLASSIFY_PROMPT = """너는 국내 패션 회사에서 일하는 직장인(기획·MD·마케팅·영업)을 위한 업계 뉴스 큐레이터다.
독자는 업계 사람이지만 해외 기사(영어·프랑스어·이탈리아어)나 금융 용어에는 익숙하지 않을 수 있다.
게시글 제목·excerpt가 외국어여도 headline·why는 한국어로 쓴다.

각 게시글을 판정하라. 게시글 필드: id, title, outlet(매체), region(kr 국내 / global 해외),
brief(true면 우리가 원문을 읽지 못하는 글 — 근거는 제목·매체·excerpt뿐), excerpt.

카테고리 (cat):
- brand: 브랜드·기업의 실적, 투자·인수합병, 대표·디자이너 교체, 구조조정, 사업 전략
- retail: 유통·커머스 — 백화점·플랫폼·매장·팝업·입점, 온라인 판매 방식, 수수료·정책
- market: 마케팅·소비자 — 캠페인, 모델 발탁, 협업, 소비 동향·조사, 인플루언서
- material: 소재·생산 — 원단, 공급망, 공장·소싱, 관세·무역, 지속가능성
- people: 인물·인터뷰 — 업계 인물의 인터뷰·칼럼·인사이트

skip=true (cat="none"):
- 패션·뷰티·라이프스타일 업계와 무관한 글 (예: 반도체·IT 기업 인사, 정치 일반)
- 컬렉션·런웨이 리뷰, 셀럽 착장·화보, 스타일링 팁, 쇼핑 추천·할인 안내, 업계 의미 없는 신제품 소개, 광고
- 같은 소식을 다룬 글이 목록에 여럿이면 하나만 남기고 나머지. 남길 때는 brief=false(원문을 읽을 수 있는 글)를 먼저,
  그다음 국내 독자에게 더 자세한 쪽.
- 아래 '저장된 글'과 같은 소식. 단 저장된 글이 brief=true이고 이 글이 brief=false면 skip 대신 replaces로 그 id를 적는다.

skip=false이면:
- headline: 한국어 45자 이내. 원제목 직역이 아니라 업계 사람이 바로 이해할 한 줄. 회사·브랜드명·숫자는 살린다.
  과장·낚시 금지. 예: "버버리, 중국·미국 덕에 2분기 매장 매출 4% 늘어"
- tag: 10자 이내 꼬리표 (예: "실적", "인수", "팝업", "인사", "관세", "인터뷰")
- why: 한국어 60자 이내, "~해요/~합니다"체로 국내 패션 회사가 왜 지금 볼 만한지
- brief=true인 글은 headline·why에 제목·excerpt에 없는 숫자·이유·인명·회사명을 쓰지 않는다.
- buzz: 업계에서 크게 화제인 소식이면 true — 이 목록이나 저장된 글에서 2곳 이상 매체가 같은 소식을 다뤘거나,
  매출 상위 그룹(LVMH·케링·에르메스·리치몬트·나이키·아디다스·인디텍스·H&M·유니클로(패스트리테일링),
  국내는 삼성물산 패션·한섬·LF·신세계인터내셔날·무신사 등)의 실적·인수·대표 교체. 그 외 false.
- pick: 0~10 우선순위. 국내 패션 회사의 의사결정에 영향이 클수록, 화제일수록 높게. brief=true인 글은 0.
- replaces: 위 규칙(저장된 brief 글을 원문을 읽을 수 있는 글이 대체)일 때만 그 id, 아니면 ""

목록의 모든 id에 대해 정확히 한 번씩 답하라.
"""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "skip": {"type": "boolean"},
                    "cat": {"type": "string", "enum": list(CATS) + ["none"]},
                    "headline": {"type": "string"},
                    "tag": {"type": "string"},
                    "why": {"type": "string"},
                    "buzz": {"type": "boolean"},
                    "pick": {"type": "integer"},
                    "replaces": {"type": "string"},
                },
                "required": ["id", "skip", "cat", "headline", "tag", "why", "buzz", "pick", "replaces"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


class LLMError(Exception):
    pass


class Usage:
    """회차 사용량 — 모델별 토큰, API 요청 수, web fetch 횟수. 상세는 병렬이라 잠금을 쓴다.
    입력 토큰에는 캐시 읽기·쓰기 토큰을 더한다(값이 없으면 0). thinking 토큰은 output_tokens에 포함된 값 그대로."""

    def __init__(self):
        import threading

        self._lock = threading.Lock()
        self.data = {"classify_calls": 0, "detail_items": 0, "api_requests": 0, "web_fetches": 0, "by_model": {}}

    def count(self, key: str) -> None:
        with self._lock:
            self.data[key] += 1

    def add(self, resp) -> None:
        u = getattr(resp, "usage", None)
        model = getattr(resp, "model", None) or "unknown"
        n = lambda k: int(getattr(u, k, 0) or 0)   # noqa: E731
        with self._lock:
            self.data["api_requests"] += 1
            m = self.data["by_model"].setdefault(model, {"input_tokens": 0, "output_tokens": 0})
            if u is not None:
                m["input_tokens"] += n("input_tokens") + n("cache_creation_input_tokens") + n("cache_read_input_tokens")
                m["output_tokens"] += n("output_tokens")
                stu = getattr(u, "server_tool_use", None)
                self.data["web_fetches"] += int(getattr(stu, "web_fetch_requests", 0) or 0) if stu else 0


def prefilter(cands: list[dict]) -> list[dict]:
    out = []
    for c in cands:
        text = f"{c['title']} {c.get('excerpt', '')}"
        if AD_WORDS.search(text):
            continue
        out.append(c)
    return out


def make_client():
    import anthropic

    return anthropic.Anthropic(max_retries=3)


def final_json(resp) -> dict:
    if resp.stop_reason == "refusal":
        raise LLMError("모델이 요청을 거절함")
    if resp.stop_reason == "max_tokens":
        raise LLMError("max_tokens 도달 — 응답 잘림")
    texts = [b.text for b in resp.content if b.type == "text"]
    if not texts:
        raise LLMError(f"텍스트 응답 없음 (stop_reason={resp.stop_reason})")
    return json.loads(texts[-1])


def ask(client, prompt: str, schema: dict, max_tokens: int, usage: Usage | None = None, **extra) -> dict:
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
        **extra,
    )
    if usage:
        usage.count("classify_calls")
        usage.add(resp)
    return final_json(resp)


def _clip(s, n: int) -> str:
    return (s or "").strip()[:n]


def classify(cands: list[dict], client, stored: list[dict] = (), usage: Usage | None = None
             ) -> tuple[list[dict], list[str]]:
    """(통과한 글, 판정 결과 건너뛴 id). 통과한 글에는 cat·headline·tag·why·buzz·pick·replaces를 붙여 pick 높은 순.
    stored: 최근 저장된 글 — 같은 소식 재수집을 거르고 대체 관계를 판정하는 데 쓴다.
    응답에서 빠진 id는 어느 쪽에도 넣지 않는다(다음 회차에 다시 판정)."""
    if not cands:
        return [], []
    live = [s for s in stored if not s.get("superseded_by")][-MAX_STORED:]
    stored_ids = {s["id"] for s in live}
    stored_brief = {s["id"] for s in live if s.get("brief")}   # 대체할 수 있는 건 원문을 못 읽은 저장 글뿐
    stored_txt = "\n".join(f"{s['id']} | {s['cat']} | {'true' if s.get('brief') else 'false'} | {s['headline']}"
                           for s in live) or "(없음)"
    lines = [
        json.dumps({k: c.get(k) for k in ("id", "title", "outlet", "region")} | {"brief": c.get("brief") is True}
                   | {"excerpt": (c.get("excerpt") or "")[:300]}, ensure_ascii=False)
        for c in cands
    ]
    prompt = f"{CLASSIFY_PROMPT}\n저장된 글 (id | cat | brief | headline):\n{stored_txt}\n\n게시글(JSON Lines):\n" + "\n".join(lines)
    data = ask(client, prompt, CLASSIFY_SCHEMA, 16000, usage)

    labels: dict[str, dict] = {}
    for r in data.get("results", []):
        labels.setdefault(r.get("id"), r)   # 같은 id가 두 번 오면 첫 답만
    out, skipped = [], []
    for c in cands:
        r = labels.get(c["id"])
        if not r:
            continue
        if r.get("skip") or r.get("cat") not in CATS or not _clip(r.get("headline"), 1):
            skipped.append(c["id"])
            continue
        rep = r.get("replaces") or ""
        brief = c.get("brief") is True
        if brief and rep in stored_ids:
            skipped.append(c["id"])        # 원문을 못 읽는 글이 저장된 글을 대체하려 하면 같은 소식의 더 나쁜 사본
            continue
        out.append(c | {
            "cat": r["cat"],
            "headline": _clip(r["headline"], 60),
            "tag": _clip(r.get("tag"), 14) or "참고",
            "why": _clip(r.get("why"), 90),
            "pick": 0 if brief else max(0, min(10, int(r.get("pick") or 0))),   # brief 글은 오늘의 픽이 될 수 없다
            "buzz": r.get("buzz") is True,
            "replaces": rep if rep in stored_brief else None,
        })
    return sorted(out, key=lambda c: -c["pick"]), skipped   # 안정 정렬 — 같은 점수는 입력 우선순위 유지
