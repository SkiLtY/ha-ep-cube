"""Build brand PNG assets from the SVG sources in this dir.

Output goes directly into custom_components/ep_cube/brand/, which HA
2026.3+ serves at /api/brands/integration/ep_cube/<image> (taking
priority over the brands-repo CDN).

Run from repo root: python docs/brand/build_assets.py
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from resvg_py import svg_to_bytes

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "custom_components" / "ep_cube" / "brand"
OUT.mkdir(parents=True, exist_ok=True)


def render_svg(svg_path: Path, width: int, height: int) -> Image.Image:
    png_bytes = bytes(svg_to_bytes(svg_path=str(svg_path), width=width, height=height))
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def render_square(svg_path: Path, size: int) -> Image.Image:
    return render_svg(svg_path, size, size)


def render_logo(svg_path: Path, height: int) -> Image.Image:
    # Logo SVG viewBox is 144.6 x 59.5 (aspect 2.43). Width is computed.
    width = round(height * 144.6 / 59.5)
    return render_svg(svg_path, width, height)


def save(img: Image.Image, name: str) -> None:
    path = OUT / name
    img.save(path, format="PNG", optimize=True)
    print(f"  {name:24s} {img.size[0]:>4}x{img.size[1]:<4} {path.stat().st_size:>6} B")


print("icons (from SVG):")
# Light theme = dark ink. SVG file: icon-source-light.svg
icon_light = HERE / "icon-source-light.svg"
save(render_square(icon_light, 256), "icon.png")
save(render_square(icon_light, 512), "icon@2x.png")

# Dark theme = white ink. SVG file: icon-source-dark.svg
icon_dark = HERE / "icon-source-dark.svg"
save(render_square(icon_dark, 256), "dark_icon.png")
save(render_square(icon_dark, 512), "dark_icon@2x.png")

print("\nlogos (from SVG):")
# logo-source-light.svg: dark ink, for light theme (no fill specified, defaults to black)
logo_light = HERE / "logo-source-light.svg"
save(render_logo(logo_light, 100), "logo.png")
save(render_logo(logo_light, 200), "logo@2x.png")

# logo-source-dark.svg: white ink, for dark theme
logo_dark = HERE / "logo-source-dark.svg"
save(render_logo(logo_dark, 100), "dark_logo.png")
save(render_logo(logo_dark, 200), "dark_logo@2x.png")

print(f"\nDone. 8 files in {OUT.relative_to(REPO)}/")
