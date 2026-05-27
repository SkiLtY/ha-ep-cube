# Brand assets

SVG sources + build script for the brand PNGs served at
`/api/brands/integration/ep_cube/<image>` in HA 2026.3+.

The rendered PNGs live in [`custom_components/ep_cube/brand/`](../../custom_components/ep_cube/brand/)
and ship inside the HACS zip — HA detects them automatically and they
take priority over the `home-assistant/brands` CDN. No `manifest.json`
changes required.

> Historical note: this repo originally targeted a PR to
> [`home-assistant/brands`](https://github.com/home-assistant/brands)
> (see [PR #10389](https://github.com/home-assistant/brands/pull/10389),
> auto-closed). That route is no longer accepted for custom integrations
> per the [Feb 2026 brands-proxy-api announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).

## Source files

SVG sources are the only build inputs — `build_assets.py` renders all 8
PNGs from them. Filenames follow HA's target-theme convention (e.g.
`icon-source-light.svg` = variant for the *light* theme = dark ink).

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

Renders 8 PNGs into `custom_components/ep_cube/brand/` via `resvg-py`
(vector → raster). Requires `pip install resvg-py pillow`.

| Output PNG | Size | Rendered from |
|---|---|---|
| `icon.png` | 256 × 256 | `icon-source-light.svg` |
| `icon@2x.png` | 512 × 512 | `icon-source-light.svg` |
| `dark_icon.png` | 256 × 256 | `icon-source-dark.svg` |
| `dark_icon@2x.png` | 512 × 512 | `icon-source-dark.svg` |
| `logo.png` | 242 × 100 | `logo-source-light.svg` |
| `logo@2x.png` | 484 × 200 | `logo-source-light.svg` |
| `dark_logo.png` | 242 × 100 | `logo-source-dark.svg` |
| `dark_logo@2x.png` | 484 × 200 | `logo-source-dark.svg` |

All outputs are RGBA with transparent backgrounds.

## Source provenance

PNGs are rendered from Canadian Solar's official EP Cube logo downloads:
https://www.epcube.com/en-UK/support/document?tagCode=canadian-solar-ep-cube-logos
The integration is independent — not affiliated with or endorsed by
Canadian Solar.
