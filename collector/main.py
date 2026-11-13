"""3시간마다 진입점: 수집 → 광고 필터 → 중복 제거 → 분류 → 상세 작성 → 저장(대체 반영) → 정리."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import classify, detail, store
from .sources import feeds, gnews, krsite
from .sources.base import Context

log = logging.getLogger("collector")

SOURCES = [("feeds", feeds), ("krsite", krsite), ("gnews", gnews)]
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
SKIP_MEMORY = timedelta(days=8)   # 수집 창(48시간)·보관(7일)보다 길게 — 건너뛴 글이 다시 들어오지 않게
GROUP = {"feed": "feeds"}   # 글의 source 값 → status.sources 키 (krsite·gnews는 이름이 같음)
DETAIL_PER_RUN = 5            # 회차당 상세 최대 (5×8회 = 하루 40)
BRIEF_SLOTS = 2               # 그중 brief 글 몫 — pick이 없어 늘 뒤로 밀리므로 먼저 준다
DETAIL_PER_DAY = 40           # 최근 24시간 상세 호출 상한(성공·근거 부족·실패 모두 셈) — 수동 실행이 겹칠 때의 안전장치
FAIL_LIMIT = 2                # 상세가 예외로 이만큼 실패하면 건너뛴 글로 기억
DAY = timedelta(hours=24)


def pick_for_detail(labeled: list[dict], n: int) -> list[dict]:
    """pick 순으로 정렬된 분류 결과에서 상세 n건. brief 글에 BRIEF_SLOTS자리를 먼저 주고, 남는 자리는 서로 채운다."""
    briefs = [c for c in labeled if c.get("brief")]
    normal = [c for c in labeled if not c.get("brief")]
    # 자리가 줄면(하루 상한 막판) brief 몫부터 줄인다: 5자리 → brief 2, 4 → 1, 3 이하 → 0 (남으면 아래에서 채움)
    take_b = min(BRIEF_SLOTS, len(briefs), max(0, n - (DETAIL_PER_RUN - BRIEF_SLOTS)))
    take_n = min(n - take_b, len(normal))
    take_b = min(len(briefs), n - take_n)
    chosen = {id(c) for c in briefs[:take_b] + normal[:take_n]}
    return [c for c in labeled if id(c) in chosen]


def _recent(stamps, now: datetime) -> list:
    return [t for t in stamps if now - store.parse_iso(t if isinstance(t, str) else t["at"]) < DAY]


def run(now: datetime | None = None, data_dir: Path = DATA, client=None, sources=SOURCES) -> dict:
    now = now or datetime.now(timezone.utc)
    items_path, status_path, skipped_path = data_dir / "items.json", data_dir / "status.json", data_dir / "skipped.json"
    failures_path = data_dir / "failures.json"
    items = store.load_items(items_path)
    prev_status = store.load_status(status_path)
    detail_calls = _recent(prev_status.get("detail_calls") or [], now)      # 최근 24시간 상세 호출 시각
    usage_24h = _recent(prev_status.get("usage_24h") or [], now)
    failures = json.loads(failures_path.read_text(encoding="utf-8")) if failures_path.exists() else {}
    failures = {k: [t for t in v if now - store.parse_iso(t) < SKIP_MEMORY] for k, v in failures.items()}
    failures = {k: v for k, v in failures.items() if v}
    # 분류에서 빠졌거나 근거가 부족했던 글 — 다시 보내지 않는다 (화면이 읽지 않는 별도 파일)
    prev_skipped = json.loads(skipped_path.read_text(encoding="utf-8")) if skipped_path.exists() else {}
    skipped = {k: v for k, v in prev_skipped.items() if now - store.parse_iso(v) < SKIP_MEMORY}
    if client is None and os.environ.get("ANTHROPIC_API_KEY"):
        client = classify.make_client()
    ctx = Context(now=now, known_ids={it["id"] for it in items} | set(skipped))

    # 1. 수집 — 소스별로 실패를 가둔다
    report: dict[str, dict] = {}
    cands: list[dict] = []
    for name, mod in sources:
        try:
            got = mod.fetch(ctx)
            cands += got
            report[name] = {"fetched": len(got)}
            log.info("수집 %s: %d건", name, len(got))
        except Exception as e:
            report[name] = {"fetched": 0, "error": f"{type(e).__name__}: {e}"[:300]}
            log.warning("수집 실패 %s: %s", name, e)
    for name in report:
        if ctx.notes.get(name):
            report[name]["note"] = "; ".join(ctx.notes[name])[:300]
        if name in ctx.notes.get("outlets", {}):
            report[name]["outlets"] = ctx.notes["outlets"][name]

    # 2~3. 광고 필터, 중복 제거, 우선순위
    filtered = classify.prefilter(cands)
    fresh = [c for c in store.dedupe_candidates(filtered, items) if c["id"] not in skipped]
    fresh = store.rank_for_run(fresh)[: classify.MAX_TO_CLASSIFY]
    log.info("후보 %d → 광고 필터 %d → 신규 %d", len(cands), len(filtered), len(fresh))

    # 4~5. 분류 → 상세 → 저장
    added: list[dict] = []
    notes: list[str] = []
    labeled_n = deferred = 0
    usage = classify.Usage()
    room = min(DETAIL_PER_RUN, DETAIL_PER_DAY - len(detail_calls))
    if client is None:
        notes.append("ANTHROPIC_API_KEY 없음 — 분류·저장 건너뜀")
        log.warning(notes[-1])
    elif fresh and room <= 0:
        notes.append(f"최근 24시간 상세 {len(detail_calls)}회 — 하루 상한이라 이번 회차는 분류도 건너뜀")
        log.warning(notes[-1])
    elif fresh:
        try:
            labeled, judged_out = classify.classify(fresh, client, store.recent(items, now, 24 * 7), usage)
            labeled_n = len(labeled)
            for sid in judged_out:
                skipped[sid] = store.iso(now)
            batch = pick_for_detail(labeled, room)
            deferred = labeled_n - len(batch)    # 자리가 없어 다음 회차에 다시 분류될 글
            detail_calls += [store.iso(now)] * len(batch)
            written, errors, thin, failed = detail.write_all(batch, client, usage)
            for sid in thin:
                skipped[sid] = store.iso(now)
            items, added = store.add_items(items, written, now)
            # 상세는 왔지만 저장 검증(핵심 줄 수 등)에서 떨어진 글도 실패로 센다 — 안 그러면 매 회차 다시 상세를 부른다
            rejected = [w["id"] for w in written if w["id"] not in {a["id"] for a in added}]
            if rejected:
                notes.append(f"상세 형식 불량으로 저장 안 됨 {len(rejected)}건: " + ", ".join(rejected)[:200])
            for fid in failed + rejected:
                failures.setdefault(fid, []).append(store.iso(now))
                if len(failures[fid]) >= FAIL_LIMIT:
                    skipped[fid] = store.iso(now)
            if errors:
                notes.append(f"상세 실패 {len(errors)}건: " + "; ".join(errors)[:300])
            log.info("분류 통과 %d → 상세 %d → 저장 %d", labeled_n, len(written), len(added))
        except Exception as e:
            notes.append(f"분류 실패: {type(e).__name__}: {e}"[:300])
            log.warning(notes[-1])
    if usage.data["api_requests"]:
        usage_24h.append({"at": store.iso(now), "usage": usage.data})

    items = store.prune(items, now)
    for name in report:
        report[name]["kept"] = sum(1 for a in added if GROUP.get(a["source"], a["source"]) == name)
    new_status = {
        "last_run": store.iso(now),
        "sources": report,
        "run": {"candidates": len(cands), "ad_filtered": len(filtered), "new": len(fresh), "labeled": labeled_n,
                "deferred": deferred, "added": len(added), "total": len(items), "skip_memory": len(skipped),
                "usage": usage.data, **({"note": " / ".join(notes)} if notes else {})},
        # 공개 파일 — 시각·건수·토큰 수만 둔다
        "detail_calls": detail_calls,
        "usage_24h": usage_24h,
    }
    store.save_json(items_path, store.items_doc(items))
    store.save_json(status_path, new_status)
    store.save_json(skipped_path, skipped)
    store.save_json(failures_path, failures)
    log.info("완료: 추가 %d, 전체 %d", len(added), len(items))
    return new_status


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
