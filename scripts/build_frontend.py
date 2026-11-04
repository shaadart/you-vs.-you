"""Build the static frontend for a separate host (docs/DEPLOYMENT.md).

The page is one self-contained HTML file plus bundled fonts. This copies them to a
directory a static host can serve, points the page at the local backend, and writes the
host's response headers (Cloudflare Pages `_headers` format).
Usage: uv run python scripts/build_frontend.py [--out dist] [--api-base http://127.0.0.1:8765]
"""

import argparse
from importlib.resources import files
from pathlib import Path

from pco.play.server import parse_origins

PLACEHOLDER = '<meta name="pco-api" content="">'

# The page uses inline <script> and <style> and nothing remote. It may talk to the
# backend and nowhere else.
HEADERS = """/*
  Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src 'self'; connect-src {api}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
  X-Content-Type-Options: nosniff
  Referrer-Policy: no-referrer

/
  Cache-Control: no-cache

/index.html
  Cache-Control: no-cache

/static/fonts/*
  Cache-Control: public, max-age=31536000, immutable
"""


def build(out: Path, api_base: str) -> list[Path]:
    (api_base,) = parse_origins([api_base])
    static = files("pco.play").joinpath("static")
    page = static.joinpath("index.html").read_text(encoding="utf-8")
    if PLACEHOLDER not in page:
        raise SystemExit(f"error: {PLACEHOLDER} not found in the page")

    fonts = out / "static" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    written = [out / "index.html", out / "_headers"]
    written[0].write_text(
        page.replace(PLACEHOLDER, f'<meta name="pco-api" content="{api_base}">', 1),
        encoding="utf-8",
    )
    written[1].write_text(HEADERS.format(api=api_base), encoding="utf-8")
    for font in sorted(static.joinpath("fonts").iterdir(), key=lambda f: f.name):
        target = fonts / font.name
        target.write_bytes(font.read_bytes())
        written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out",
        default="dist",
        help="output directory (files are overwritten, nothing is deleted; default: dist)",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8765",
        help="origin of the local backend the page calls (default: http://127.0.0.1:8765)",
    )
    args = parser.parse_args()
    try:
        written = build(Path(args.out), args.api_base)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"wrote {len(written)} files to {args.out}/ (page calls {args.api_base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
