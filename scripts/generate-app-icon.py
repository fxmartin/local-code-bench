# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Generate the LocalCodeBench macOS app icon.

Renders the 1024x1024 master PNG (macOS-style rounded rect, speedometer arc
around a </> glyph), then emits the full .iconset and AppIcon.icns via
sips/iconutil. Run with: uv run scripts/generate-app-icon.py
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "macos" / "Resources"

# All geometry is authored on a 1024 canvas and drawn at SCALE for antialiasing.
SIZE = 1024
SCALE = 4
S = SIZE * SCALE

# macOS Big Sur+ icon grid: 824pt rounded rect centered on the 1024 canvas.
MARGIN = 100
CORNER_RADIUS = 184

BG_TOP = (42, 49, 98)
BG_BOTTOM = (13, 16, 40)
TRACK = (255, 255, 255, 46)
ARC_START_COLOR = (34, 211, 238)  # cyan — slow end of the dial
ARC_END_COLOR = (163, 230, 53)  # lime — fast end of the dial
GLYPH = (245, 248, 255, 255)

# Speedometer geometry: PIL angles are clockwise with 0° at 3 o'clock, so
# 150°→390° sweeps up through 12 o'clock, leaving the classic gap at the bottom.
DIAL_START, DIAL_END = 150, 390
PROGRESS_END = 352  # needle-high: most of the dial lit
CX, CY = 512, 540
DIAL_R = 296
TRACK_W = 58


def sp(v: float) -> float:
    return v * SCALE


def lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_background(img: Image.Image) -> None:
    rect = Image.new("L", (S, S), 0)
    ImageDraw.Draw(rect).rounded_rectangle(
        [sp(MARGIN), sp(MARGIN), sp(SIZE - MARGIN), sp(SIZE - MARGIN)],
        radius=sp(CORNER_RADIUS),
        fill=255,
    )

    gradient = Image.new("RGB", (S, S))
    top, bottom = sp(MARGIN), sp(SIZE - MARGIN)
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(S):
        t = min(1.0, max(0.0, (y - top) / (bottom - top)))
        grad_draw.line([(0, y), (S, y)], fill=lerp_color(BG_TOP, BG_BOTTOM, t))
    img.paste(gradient, (0, 0), rect)


def arc_point(angle_deg: float, radius: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return sp(CX) + sp(radius) * math.cos(a), sp(CY) + sp(radius) * math.sin(a)


def draw_dial(img: Image.Image) -> None:
    bbox = [sp(CX - DIAL_R), sp(CY - DIAL_R), sp(CX + DIAL_R), sp(CY + DIAL_R)]
    cap_r = sp(TRACK_W) / 2

    # Translucent track on its own layer: ImageDraw writes RGBA values instead
    # of blending, so drawing TRACK directly would punch holes in the icon.
    track = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    td = ImageDraw.Draw(track)
    td.arc(bbox, DIAL_START, DIAL_END, fill=TRACK, width=round(sp(TRACK_W)))
    for angle in (DIAL_START, DIAL_END):
        x, y = arc_point(angle, DIAL_R)
        td.ellipse([x - cap_r, y - cap_r, x + cap_r, y + cap_r], fill=TRACK)
    img.alpha_composite(track)

    # Gradient progress arc, drawn as overlapping short opaque segments.
    draw = ImageDraw.Draw(img)
    steps = 160
    span = PROGRESS_END - DIAL_START
    for i in range(steps):
        t0, t1 = i / steps, (i + 1) / steps
        color = lerp_color(ARC_START_COLOR, ARC_END_COLOR, t0)
        draw.arc(
            bbox,
            DIAL_START + span * t0,
            DIAL_START + span * t1 + 0.8,
            fill=color,
            width=round(sp(TRACK_W)),
        )
    for angle, color in ((DIAL_START, ARC_START_COLOR), (PROGRESS_END, ARC_END_COLOR)):
        x, y = arc_point(angle, DIAL_R)
        draw.ellipse([x - cap_r, y - cap_r, x + cap_r, y + cap_r], fill=color)


def draw_glow(img: Image.Image) -> None:
    """Soft glow behind the bright end of the arc — reads as speed."""
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    x, y = arc_point(PROGRESS_END, DIAL_R)
    r = sp(TRACK_W) * 0.85
    gd.ellipse([x - r, y - r, x + r, y + r], fill=(*ARC_END_COLOR, 110))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(sp(16))))


def rounded_line(draw: ImageDraw.ImageDraw, points: list, width: float) -> None:
    draw.line(points, fill=GLYPH, width=round(width), joint="curve")
    r = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=GLYPH)


def draw_glyph(draw: ImageDraw.ImageDraw) -> None:
    """</> centered in the dial, sized to clear the ring and the slash."""
    stroke = sp(42)
    tip = sp(205)  # chevron tip distance from center
    reach = sp(110)  # horizontal depth of each chevron
    ch_h = sp(115)  # chevron half-height
    cx, cy = sp(CX), sp(CY + 6)

    rounded_line(
        draw,
        [(cx - tip + reach, cy - ch_h), (cx - tip, cy), (cx - tip + reach, cy + ch_h)],
        stroke,
    )
    rounded_line(
        draw,
        [(cx + tip - reach, cy - ch_h), (cx + tip, cy), (cx + tip - reach, cy + ch_h)],
        stroke,
    )
    slash_dx, slash_dy = sp(50), sp(135)
    rounded_line(
        draw,
        [(cx + slash_dx, cy - slash_dy), (cx - slash_dx, cy + slash_dy)],
        stroke,
    )


def render_master() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_background(img)
    draw_dial(img)
    draw_glow(img)
    draw_glyph(ImageDraw.Draw(img))
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def build_icns(master_png: Path) -> Path:
    iconset = OUT_DIR / "AppIcon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = size * scale
            target = iconset / f"icon_{size}x{size}{suffix}.png"
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(master_png), "--out", str(target)],
                check=True,
                capture_output=True,
            )
    icns = OUT_DIR / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    shutil.rmtree(iconset)
    return icns


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master_png = OUT_DIR / "AppIcon-1024.png"
    render_master().save(master_png)
    icns = build_icns(master_png)
    print(f"wrote {master_png}")
    print(f"wrote {icns}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
