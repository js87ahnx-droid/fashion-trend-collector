"""아이콘 PNG가 시안 SVG와 같은지 확인 — 다른 렌더러(Edge 헤드리스)로 web/icon.svg를 찍어 Pillow가 그린 512 PNG와 비교.
기준: RGB 채널별 절댓값 차이 평균 ≤ 8/255 그리고 어느 채널이든 64/255 넘게 다른 픽셀 ≤ 1.5%.
기준 자체 검증: ① 같은 SVG를 Edge로 두 번 찍은 차이 ≤ 1/255 ② "fn"을 뺀 SVG(Edge) ↔ 글자 없이 그린 PNG도 기준 안.
python scripts/check_icon.py [비교 이미지 저장 폴더]   (Windows, Edge·Pillow 필요. 로컬에서만 씀)"""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_icons  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
MEAN_MAX, BIG, BIG_MAX = 8, 64, 0.015


def edge_render(svg: str, out: Path) -> Image.Image:
    page = out.with_suffix(".html")
    page.write_text(f'<!doctype html><html><body style="margin:0;overflow:hidden">{svg}</body></html>', encoding="utf-8")
    # 이 PC의 Edge 헤드리스는 스크린샷을 쓴 뒤에도 꺼지지 않는다 → 파일이 생기면 프로세스 트리를 닫는다
    proc = subprocess.Popen([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             "--force-device-scale-factor=1", f"--user-data-dir={out.parent / ('profile-' + out.stem)}",   # 찍을 때마다 새 프로필 — 앞 창이 남아 있어도 넘겨받지 않게
                             "--window-size=512,512", f"--screenshot={out}", page.as_uri()],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            if out.exists() and out.stat().st_size > 0:
                time.sleep(0.5)
                break
            if proc.poll() is not None and not out.exists():
                raise RuntimeError("Edge가 스크린샷 없이 끝남")
            time.sleep(0.5)
        else:
            raise TimeoutError("Edge 스크린샷 60초 안에 안 생김")
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    return Image.open(out).convert("RGB").crop((0, 0, 512, 512))


def compare(a: Image.Image, b: Image.Image) -> tuple[float, float]:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    px = list(diff.get_flattened_data() if hasattr(diff, "get_flattened_data") else diff.getdata())
    mean = sum(sum(p) for p in px) / (len(px) * 3)
    big = sum(1 for p in px if max(p) > BIG) / len(px)
    return mean, big


def ok(mean: float, big: float) -> bool:
    return mean <= MEAN_MAX and big <= BIG_MAX


def main() -> int:
    keep = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    svg = (ROOT / "web" / "icon.svg").read_text(encoding="utf-8")
    svg_no_text = "\n".join(line for line in svg.splitlines() if "<text" not in line)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        t = Path(tmp)
        e1, e2 = edge_render(svg, t / "e1.png"), edge_render(svg, t / "e2.png")
        e_nt = edge_render(svg_no_text, t / "e_nt.png")
        png = Image.open(ROOT / "web" / "icon-512.png").convert("RGB")
        png_nt = make_icons.icon(512, text=False)
        results = {
            "Edge 두 번 (렌더러 흔들림)": compare(e1, e2),
            "글자 없는 SVG ↔ 글자 없는 PNG (도형만)": compare(e_nt, png_nt),
            "web/icon.svg ↔ web/icon-512.png": compare(e1, png),
        }
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            side = Image.new("RGB", (1040, 512), "white")
            side.paste(e1, (0, 0)); side.paste(png, (528, 0))
            side.save(keep / "icon-compare.png")
            ImageChops.difference(e1, png).point(lambda v: min(255, v * 4)).save(keep / "icon-diff-x4.png")
    for name, (mean, big) in results.items():
        print(f"{name}: 평균 차이 {mean:.2f}/255, 64 넘게 다른 픽셀 {big * 100:.2f}%")
    calm = results["Edge 두 번 (렌더러 흔들림)"][0] <= 1
    shapes = ok(*results["글자 없는 SVG ↔ 글자 없는 PNG (도형만)"])
    final = ok(*results["web/icon.svg ↔ web/icon-512.png"])
    print(f"기준 검증: 렌더러 흔들림 {'통과' if calm else '실패'}, 도형만 {'통과' if shapes else '실패'} / 최종 {'통과' if final else '실패'}")
    return 0 if calm and shapes and final else 1


if __name__ == "__main__":
    sys.exit(main())
