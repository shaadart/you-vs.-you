"""Draw the app icon: two overlapping discs, the human ochre and the machine cobalt of the
page's palette, on the page's dark ground. Writes packaging/icons/icon.{png,ico,icns}.

    uv run --group build python packaging/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).parent / "icons"
SIZE = 1024
GROUND, OCHRE, COBALT = (25, 25, 23), (227, 174, 82), (147, 166, 255)


def disc(color, cx, cy, r):
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), fill=color + (255,))
    return layer


def main() -> None:
    OUT.mkdir(exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, SIZE - 1, SIZE - 1), radius=230, fill=GROUND + (255,)
    )
    left, right = disc(OCHRE, 390, 512, 250), disc(COBALT, 634, 512, 250)
    both = ImageChops.multiply(left, right)  # where they meet: the overlap, darker
    img.alpha_composite(left)
    img.alpha_composite(right)
    overlap = Image.new("RGBA", (SIZE, SIZE), GROUND + (255,))
    overlap.putalpha(both.getchannel("A"))
    img.alpha_composite(overlap)
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)])
    img.save(OUT / "icon.icns")


if __name__ == "__main__":
    main()
