"""홈 화면 아이콘 PNG 생성 — 시안 A · 옷걸이(web/icon.svg와 같은 좌표·색). 한 번 만들어 web/에 커밋, CI에선 쓰지 않음.
python scripts/make_icons.py [--no-text] [출력 폴더]   (Pillow, Georgia Bold 필요)
--no-text: "fn" 글자 없이 도형만 (아이콘 비교 기준 검증용)"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WEB = Path(__file__).resolve().parents[1] / "web"
FONT = "C:/Windows/Fonts/georgiab.ttf"     # SVG의 Georgia 700
BG, FG = (0x8A, 0x3B, 0x2E), (0xF7, 0xF1, 0xEA)
STROKE = 22
SS = 4                                     # 크게 그린 뒤 줄여 가장자리를 매끈하게


def quad(p0, p1, p2, n=16):
    return [tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t ** 2 * c for a, b, c in zip(p0, p1, p2))
            for t in (i / n for i in range(1, n + 1))]


def cubic(p0, p1, p2, p3, n=24):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3)) for t in (i / n for i in range(1, n + 1))]


def hook() -> list[tuple[float, float]]:
    """M256 150 a34 34 0 1 1 34 34 c-18 0-34 10-34 26 v12 — 중심 (290,150) 반지름 34, 왼쪽 → 위 → 오른쪽 → 아래로 270도."""
    pts = [(290 + 34 * math.cos(math.radians(a)), 150 + 34 * math.sin(math.radians(a))) for a in range(180, 451, 5)]
    pts += cubic((290, 184), (272, 184), (256, 194), (256, 210))
    pts.append((256, 222))
    return pts


def body() -> list[tuple[float, float]]:
    """M256 222 L104 330 q-14 12 4 18 h296 q18-6 4-18 Z (닫힌 선)"""
    pts = [(256, 222), (104, 330)]
    pts += quad((104, 330), (90, 342), (108, 348))
    pts.append((404, 348))
    pts += quad((404, 348), (422, 342), (408, 330))
    pts.append((256, 222))
    return pts


def stroke(d: ImageDraw.ImageDraw, pts, k: float) -> None:
    """두께 STROKE의 선 + 둥근 이음·끝 (모든 꼭짓점에 원을 찍어 둥글게)."""
    w = STROKE * k
    sc = [(x * k, y * k) for x, y in pts]
    d.line(sc, fill=FG, width=round(w))
    for x, y in sc:
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=FG)


def icon(n: int, text: bool = True) -> Image.Image:
    s = n * SS
    k = s / 512
    img = Image.new("RGB", (s, s), BG)
    d = ImageDraw.Draw(img)
    stroke(d, hook(), k)
    stroke(d, body(), k)
    if text:
        font = ImageFont.truetype(FONT, round(78 * k))
        d.text((430 * k, 452 * k), "fn", font=font, fill=FG, anchor="rs")   # text-anchor=end, y=기준선
    return img.resize((n, n), Image.LANCZOS)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--no-text"]
    out = Path(args[0]) if args else WEB
    for n in (180, 192, 512):
        icon(n, "--no-text" not in sys.argv).save(out / f"icon-{n}.png", optimize=True)
        print(out / f"icon-{n}.png")
