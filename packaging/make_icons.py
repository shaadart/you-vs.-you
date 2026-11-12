"""Draw the Imperial V4 Pawn mark (M2) across app icons and transparent brand assets.

Writes packaging/icons/icon.{png,ico,icns}, logo.svg, logo-graphite.png, and logo-paper.png.

    uv run --group build python packaging/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "icons"
SIZE = 1024
SUPER = SIZE * 2  # 2x supersample for crisp Lanczos anti-aliasing

# Palette tokens from docs/DESIGN.md
GROUND = (25, 25, 23)
BONE = (236, 232, 224)
COBALT_DARK = (147, 166, 255)
INK_LIGHT = (25, 24, 22)
COBALT_LIGHT = (39, 71, 217)

PAWN_SVG_PATH = (
    "M60 12 "
    "C54.2 12 50.5 16.2 50.5 21.5 "
    "C50.5 25.8 53.2 29.6 55.5 31.4 "
    "L55 33 L40 33 L39 34.2 L40.2 36.2 "
    "C45.5 37.8 49.5 39 51 40.5 "
    "C50.2 56 45.5 73 37 87 "
    "L34 87 L34 96 L30 96 L30 108 L60 108 "
    "L90 108 L90 96 L86 96 L86 87 L81 87 L81 78 "
    "L78 78 L78 69 L75 69 L75 60 L72 60 L72 48 "
    "L69 48 L69 39 L81 39 L81 33 L66 33 L66 30 "
    "L69 30 L69 27 L72 27 L72 18 L69 18 L69 15 "
    "L66 15 L66 12 Z"
)

RELIEF_RECTS = (
    (66, 15, 3, 3),
    (69, 18, 3, 9),
    (66, 27, 3, 3),
    (78, 33, 3, 6),
    (69, 39, 12, 3),
    (69, 48, 3, 12),
    (72, 60, 3, 9),
    (75, 69, 3, 9),
    (78, 78, 3, 9),
    (83, 87, 3, 9),
    (87, 96, 3, 12),
)


def _cubic(p0, p1, p2, p3, n=28):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        pts.append(
            (
                mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
                mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return pts


def _dedup_int(pts):
    out = []
    for x, y in pts:
        ix, iy = int(round(x)), int(round(y))
        if not out or out[-1] != (ix, iy):
            out.append((ix, iy))
    if len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def _imperial_poly():
    left = [
        (60, 12),
        *_cubic((60, 12), (54.2, 12), (50.5, 16.2), (50.5, 21.5), 18),
        *_cubic((50.5, 21.5), (50.5, 25.8), (53.2, 29.6), (55.5, 31.4), 12),
        (55, 33),
        (40, 33),
        (39, 34.2),
        (40.2, 36.2),
        *_cubic((40.2, 36.2), (45.5, 37.8), (49.5, 39), (51, 40.5), 12),
        *_cubic((51, 40.5), (50.2, 56), (45.5, 73), (37, 87), 28),
        (34, 87),
        (34, 96),
        (30, 96),
        (30, 108),
        (60, 108),
    ]
    right = [
        (90, 108),
        (90, 96),
        (86, 96),
        (86, 87),
        (81, 87),
        (81, 78),
        (78, 78),
        (78, 69),
        (75, 69),
        (75, 60),
        (72, 60),
        (72, 48),
        (69, 48),
        (69, 39),
        (81, 39),
        (81, 33),
        (66, 33),
        (66, 30),
        (69, 30),
        (69, 27),
        (72, 27),
        (72, 18),
        (69, 18),
        (69, 15),
        (66, 15),
        (66, 12),
    ]
    return left + right


def _draw_mark(
    img: Image.Image,
    fg: tuple[int, int, int],
    accent: tuple[int, int, int],
    box: tuple[float, float],
) -> None:
    offset, span = box
    k = span / 120.0
    draw = ImageDraw.Draw(img)
    pts = _dedup_int([(offset + x * k, offset + y * k) for x, y in _imperial_poly()])
    draw.polygon(pts, fill=fg + (255,))
    for x, y, w, h in RELIEF_RECTS:
        draw.rectangle(
            (offset + x * k, offset + y * k, offset + (x + w) * k, offset + (y + h) * k),
            fill=accent + (255,),
        )


def _write_svg() -> None:
    rects = "".join(
        f'<rect class="relief" x="{x}" y="{y}" width="{w}" height="{h}"/>'
        for x, y, w, h in RELIEF_RECTS
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">\n'
        "  <style>\n"
        "    .pawn { fill: var(--ink, #191816); }\n"
        "    .relief { fill: var(--machine, #2747D9); }\n"
        "    @media (prefers-color-scheme: dark) {\n"
        "      .pawn { fill: var(--ink, #ECE8E0); }\n"
        "      .relief { fill: var(--machine, #93A6FF); }\n"
        "    }\n"
        "  </style>\n"
        f'  <path class="pawn" d="{PAWN_SVG_PATH}"/>\n'
        f"  {rects}\n"
        "</svg>\n"
    )
    (OUT / "logo.svg").write_text(svg, encoding="utf-8")


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # App icon on dark Graphite rounded tile (desktop launchers, dock, .ico/.icns)
    app_img = Image.new("RGBA", (SUPER, SUPER), (0, 0, 0, 0))
    ImageDraw.Draw(app_img).rounded_rectangle(
        (0, 0, SUPER - 1, SUPER - 1), radius=460, fill=GROUND + (255,)
    )
    _draw_mark(app_img, BONE, COBALT_DARK, (256.0, 1536.0))
    app_final = app_img.resize((SIZE, SIZE), Image.LANCZOS)
    app_final.save(OUT / "icon.png")
    app_final.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    app_final.save(OUT / "icon.icns")

    # Standalone transparent-background brand marks (dark-room and light-room variants + vector SVG)
    for filename, fg, accent in (
        ("logo-graphite.png", BONE, COBALT_DARK),
        ("logo-paper.png", INK_LIGHT, COBALT_LIGHT),
    ):
        mark = Image.new("RGBA", (SUPER, SUPER), (0, 0, 0, 0))
        _draw_mark(mark, fg, accent, (0.0, float(SUPER)))
        mark.resize((SIZE, SIZE), Image.LANCZOS).save(OUT / filename)

    _write_svg()


if __name__ == "__main__":
    main()
