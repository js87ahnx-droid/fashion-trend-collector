"""실제 네트워크로 각 소스를 한 번씩 불러 건수만 출력한다 (저장·LLM 호출 없음).
매체별 줄: 받은 건수 (스타일 잡지는 '필터 전 → 필터 뒤')."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.main import SOURCES  # noqa: E402
from collector.sources.base import Context  # noqa: E402

ctx = Context(now=datetime.now(timezone.utc))
for name, mod in SOURCES:
    try:
        items = mod.fetch(ctx)
        sample = items[0]["title"][:60] if items else "-"
        print(f"{name:10} {len(items):3}건  예: {sample}")
    except Exception as e:
        print(f"{name:10} 실패: {e}")
    for outlet, c in ctx.notes.get("outlets", {}).get(name, {}).items():
        counts = f"{c['before_filter']:3} → {c['fetched']:3}" if "before_filter" in c else f"      {c['fetched']:3}"
        print(f"  {outlet:14} {counts}건  {c.get('error', '')}")
