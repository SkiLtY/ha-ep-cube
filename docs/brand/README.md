# Brand assets

Source assets for the eventual [`home-assistant/brands`](https://github.com/home-assistant/brands)
PR that registers `ep_cube` with proper iconography in the HA UI and HACS listings.

These files are **not shipped inside the HACS zip** (release workflow
zips only `custom_components/ep_cube/`). They live here for version
control + traceability when we build the brands PR.

## Source files

SVG sources are the only inputs — `build_assets.py` renders all 8
brands target files from them. Filenames follow HA's target-theme
convention (e.g. `icon-source-light.svg` = variant for the *light*
theme = dark ink).

| File | viewBox | Ink | Description |
|---|---|---|---|
| `icon-source-light.svg` | 500 × 500 | dark, transparent bg | Sun-burst symbol for light theme |
| `icon-source-dark.svg` | 500 × 500 | white, transparent bg | Sun-burst symbol for dark theme |
| `logo-source-light.svg` | 144.6 × 59.5 | dark, transparent bg | Full lockup for light theme |
| `logo-source-dark.svg` | 144.6 × 59.5 | white, transparent bg | Full lockup for dark theme |

## Build

```bash
python docs/brand/build_assets.py
```

Renders 8 PNGs into `docs/brand/output/` via `resvg-py` (vector → raster).
Requires `pip install resvg-py pillow`. These are the exact files that
get copied into `custom_integrations/ep_cube/` on the brands fork.

## Mapping to home-assistant/brands

Target paths in the brands repo are under `custom_integrations/ep_cube/`.
HA brands convention: filenames refer to **target theme**, not artwork
colour — `logo.png` works on light backgrounds (= black ink) and
`dark_logo.png` works on dark backgrounds (= white ink).

| Brands target | Output size | Rendered from |
|---|---|---|
| `icon.png` | 256 × 256 | `icon-source-light.svg` |
| `icon@2x.png` | 512 × 512 | `icon-source-light.svg` |
| `dark_icon.png` | 256 × 256 | `icon-source-dark.svg` |
| `dark_icon@2x.png` | 512 × 512 | `icon-source-dark.svg` |
| `logo.png` | 242 × 100 | `logo-source-light.svg` |
| `logo@2x.png` | 484 × 200 | `logo-source-light.svg` |
| `dark_logo.png` | 242 × 100 | `logo-source-dark.svg` |
| `dark_logo@2x.png` | 484 × 200 | `logo-source-dark.svg` |

All output PNGs are RGBA with transparent backgrounds.

## Brand acknowledgement

`home-assistant/brands` accepts third-party manufacturer logos by
convention. The PR description should acknowledge that the assets are
sourced from Canadian Solar's public EP Cube brand pack and included
under the brands repo's third-party-manufacturer convention.
