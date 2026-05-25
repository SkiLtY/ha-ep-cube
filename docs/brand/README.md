# Brand assets

Source assets for the eventual [`home-assistant/brands`](https://github.com/home-assistant/brands)
PR that registers `ep_cube` with proper iconography in the HA UI and HACS listings.

These files are **not shipped inside the HACS zip** (release workflow
zips only `custom_components/ep_cube/`). They live here for version
control + traceability when we build the brands PR.

## Source files

All assets are stored as-supplied by the EP Cube / Canadian Solar brand
pack — no resizing, recolouring, or cropping has been done in-repo.
**Filenames use the artwork colour convention** (e.g. `logo-light` =
"the light-coloured / white logo"), **not** HA's target-theme convention.
The mapping table below resolves the difference at PR time.

| File | Dimensions | Format | Description |
|---|---|---|---|
| `icon-source.png` | 300 × 300 | white-on-opaque-black | Sun-burst symbol only |
| `icon-source-inverted.png` | 300 × 300 | black-on-opaque-white | RGB-inverted from `icon-source.png` for HA light theme |
| `icon-lockup-source.png` | 2000 × 2000 | white-on-opaque-black | Full lockup (symbol + "CanadianSolar" + "EP CUBE"). Reference only — brands review prefers symbol-only icons. |
| `logo-light-source.png` | 262 × 100 | white pixels, transparent bg | Wide lockup, white artwork |
| `logo-dark-source.png` | 262 × 100 | black pixels, transparent bg | Wide lockup, black artwork |

## Mapping to home-assistant/brands

Target paths in the brands repo are under
`custom_integrations/ep_cube/`. HA brands convention: filenames refer
to **target theme**, not artwork colour — so `logo.png` works on light
backgrounds (= black ink) and `dark_logo.png` works on dark backgrounds
(= white ink). This is the inverse of our source naming; do not be
confused.

| Brands target | Spec | Source file | Notes |
|---|---|---|---|
| `icon.png` | 256 × 256 PNG | `icon-source-inverted.png` | Resize 300 → 256. Opaque white bg renders cleanly on HA light theme. |
| `icon@2x.png` | 512 × 512 PNG | `icon-source-inverted.png` | Upscale 300 → 512. Lossy — try to source a higher-res original before PR. |
| `dark_icon.png` | 256 × 256 PNG | `icon-source.png` | Resize 300 → 256. Opaque black bg, fine on HA dark theme. |
| `dark_icon@2x.png` | 512 × 512 PNG | `icon-source.png` | Upscale 300 → 512. Same caveat as `icon@2x`. |
| `logo.png` | ≤ 256 tall PNG | `logo-dark-source.png` | **Black artwork** for light theme. 100 tall = within spec. |
| `logo@2x.png` | ≤ 512 tall PNG | `logo-dark-source.png` | Upscale to ~200 tall. Within spec but undersized vs ideal. |
| `dark_logo.png` | ≤ 256 tall PNG | `logo-light-source.png` | **White artwork** for dark theme. |
| `dark_logo@2x.png` | ≤ 512 tall PNG | `logo-light-source.png` | Same as `dark_logo` doubled. |

## Open items before submitting the brands PR

1. **Higher-res icon source** — current `icon-source.png` is 300², which
   under-samples for the `@2x` 512² target. Worth asking Canadian Solar
   for the vector / 1024+ raster before submitting, or generating an SVG.
2. **Transparent-bg sun-burst** — currently both icon variants have
   opaque backgrounds (the icon sits as a solid square in the UI). HA
   brands accepts this, but transparent symbols feel cleaner. Try
   chroma-keying the black (or asking the brand owner for the source).
3. **Trademark / brand permission** — `home-assistant/brands` accepts
   third-party manufacturer logos by convention, but a note in the PR
   description acknowledging the asset source is good practice.
