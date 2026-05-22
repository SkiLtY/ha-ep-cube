"""EP Cube mobile-app login flow with puzzle-captcha solving.

Replaces the JSESSIONID-paste UX with a one-time email + password capture.
Bound at config-flow submit, then re-bound as the client's reauth callback
so 403s on token expiry trigger a silent re-login.

The flow is 4 POSTs reverse-engineered from the iOS app:

  1. POST /api/open/common/captcha/get  {clientUid}
     → {originalImageBase64, jigsawImageBase64, secretKey, token}

  2. Locate the puzzle-piece position in the background image using
     TM_CCOEFF_NORMED template matching (pure-numpy port, see
     docs/PHASE_3_2.md captcha-solver spike for derivation + why
     palette-mode images bypass the textbook `convert("L")` path).

  3. POST /api/open/common/captcha/check  {clientUid, token, pointJson}
     where pointJson is AES-ECB(secretKey, json({x,y})) base64-encoded.
     → {captchaVerification}

  4. POST /api/open/common/login  {userName, password, captchaVerification}
     → {data: {token: <Bearer>}}

Per-attempt captcha success on the cube is ~80% (spike result); we retry
up to `max_attempts` times for an effective success rate near 99%.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from io import BytesIO
from typing import Any

import aiohttp
import numpy as np
from PIL import Image

_LOGGER = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------
class CaptchaSolveError(Exception):
    """All captcha-solve attempts failed."""


class LoginError(Exception):
    """Email/password rejected by /api/open/common/login."""


# ----------------------------------------------------------------------
# Headers — copied verbatim from the iOS app (same as api.py uses).
# ----------------------------------------------------------------------
_HEADERS = {
    "User-Agent": "ReservoirMonitoring/2.1.0 (iPhone; iOS 18.3.2; Scale/3.00)",
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-GB",
}


# ----------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------
async def login(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    username: str,
    password: str,
    api_prefix: str = "/api",
    max_attempts: int = 3,
) -> str:
    """Run the full 4-POST login flow and return a Bearer token.

    Raises `CaptchaSolveError` if every captcha attempt fails, or
    `LoginError` if the credentials are rejected. Network errors propagate
    as `aiohttp.ClientError`.
    """
    base = base_url.rstrip("/") + "/" + api_prefix.strip("/")

    last_check_msg: str | None = None
    for attempt in range(1, max_attempts + 1):
        client_uid = str(uuid.uuid4())
        captcha = await _fetch_captcha(session, base, client_uid)
        x = _solve_captcha_image(
            captcha["originalImageBase64"], captcha["jigsawImageBase64"]
        )
        if x is None:
            _LOGGER.debug("captcha attempt %d: image dimensions unworkable", attempt)
            continue

        point_json = _encrypt_point(x, _CAPTCHA_REPORT_Y, captcha["secretKey"])
        check = await _post_json(
            session,
            f"{base}/open/common/captcha/check",
            {
                "clientUid": client_uid,
                "token": captcha["token"],
                "pointJson": point_json,
            },
        )
        if _check_succeeded(check):
            verification = _build_captcha_verification(
                captcha["token"], x, _CAPTCHA_REPORT_Y, captcha["secretKey"]
            )
            return await _do_login(
                session,
                base,
                username=username,
                password=password,
                verification=verification,
            )
        last_check_msg = _shortest_message(check)
        _LOGGER.debug(
            "captcha attempt %d/%d failed at /check: %s",
            attempt, max_attempts, last_check_msg,
        )

    raise CaptchaSolveError(
        f"all {max_attempts} captcha attempts failed; last /check msg: {last_check_msg!r}"
    )


def make_reauth_callback(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    username: str,
    password: str,
    api_prefix: str = "/api",
):
    """Build a callable suitable for `EPCubeClient(reauth_callback=...)`.

    Returns an awaitable producing a fresh Bearer token, or None on failure
    (in which case the client surfaces the original 403 to the caller).
    Serialised so two concurrent 403s don't race two parallel logins —
    only one captcha solve runs at a time per callback.
    """
    lock = asyncio.Lock()

    async def _reauth() -> str | None:
        async with lock:
            try:
                token = await login(
                    session,
                    base_url=base_url,
                    username=username,
                    password=password,
                    api_prefix=api_prefix,
                )
            except (CaptchaSolveError, LoginError, aiohttp.ClientError) as err:
                _LOGGER.warning("re-auth failed: %s", err)
                return None
            _LOGGER.info("re-auth succeeded — fresh bearer token acquired")
            return token

    return _reauth


# ----------------------------------------------------------------------
# Captcha solver — see docs/PHASE_3_2.md "Captcha-solver feasibility spike"
# ----------------------------------------------------------------------
# Sliders are horizontal-only; the renderer fixes y. Bobsilvio's tool uses
# 5 and the cube accepts it — y is reported back in the check payload but
# only x affects whether the match validates.
_CAPTCHA_REPORT_Y = 5


def _solve_captcha_image(original_b64: str, puzzle_b64: str) -> int | None:
    """Decode both images and return the x-pixel where the puzzle piece sits.

    Returns None if the captcha images have dimensions outside what the
    matcher can handle (puzzle larger than background in either axis after
    the small-delta height pad). The caller should retry with a fresh
    captcha in that case."""
    background = _decode_b64_image_grey(original_b64)
    puzzle = _decode_b64_image_grey(puzzle_b64)

    # ~40% of captchas come back with bg 2 px shorter than the puzzle in
    # height (renderer drops the slider track). Pad bg with its mean value
    # to match — the added rows align with the puzzle's transparent
    # (rendered as 0 after decode) regions, so accuracy is preserved.
    height_delta = puzzle.shape[0] - background.shape[0]
    if 0 < height_delta <= 4:
        pad_top = height_delta // 2
        pad_bot = height_delta - pad_top
        mean_val = int(background.mean())
        background = np.pad(
            background,
            ((pad_top, pad_bot), (0, 0)),
            mode="constant",
            constant_values=mean_val,
        )

    if puzzle.shape[0] > background.shape[0] or puzzle.shape[1] > background.shape[1]:
        return None

    res = _match_template_ccoeff_normed(background, puzzle)
    flat = int(res.argmax())
    y, x = np.unravel_index(flat, res.shape)
    return int(x)


def _match_template_ccoeff_normed(
    image: np.ndarray, template: np.ndarray
) -> np.ndarray:
    """Pure-numpy equivalent of cv2.matchTemplate(..., TM_CCOEFF_NORMED).

    Returns a 2D float64 of shape (H-h+1, W-w+1); entry [y,x] is the
    normalized cross-correlation when template's top-left sits at (y,x)
    in the image. Two tricks:
      1. Integral images give per-window sum + sum-of-squares in O(1).
      2. Zero-mean template makes the numerator a plain cross-correlation,
         which FFT computes in O(N log N) rather than O(N·n).
    """
    image = image.astype(np.float64)
    template = template.astype(np.float64)
    H, W = image.shape
    h, w = template.shape
    n = h * w

    t_zm = template - template.mean()
    t_norm_sq = float((t_zm ** 2).sum())

    integral = np.zeros((H + 1, W + 1), dtype=np.float64)
    integral[1:, 1:] = image.cumsum(axis=0).cumsum(axis=1)
    integral_sq = np.zeros((H + 1, W + 1), dtype=np.float64)
    integral_sq[1:, 1:] = (image ** 2).cumsum(axis=0).cumsum(axis=1)

    win_sum = (
        integral[h:, w:] - integral[:-h, w:] - integral[h:, :-w] + integral[:-h, :-w]
    )
    win_sq = (
        integral_sq[h:, w:]
        - integral_sq[:-h, w:]
        - integral_sq[h:, :-w]
        + integral_sq[:-h, :-w]
    )
    win_var_n = np.maximum(win_sq - (win_sum ** 2) / n, 0.0)

    fft_shape = (H + h - 1, W + w - 1)
    F_I = np.fft.rfft2(image, s=fft_shape)
    F_T = np.fft.rfft2(t_zm[::-1, ::-1], s=fft_shape)
    corr_full = np.fft.irfft2(F_I * F_T, s=fft_shape)
    numerator = corr_full[h - 1 : H, w - 1 : W]

    denom = np.sqrt(t_norm_sq * win_var_n)
    return np.where(denom > 1e-10, numerator / denom, 0.0)


def _decode_b64_image_grey(b64: str) -> np.ndarray:
    """Decode a base64 PNG to a 2D uint8 greyscale numpy array.

    The EP Cube background image is encoded in PIL "P" (palette) mode.
    `np.array(palette_image)` returns the raw palette indices, NOT the
    looked-up RGB values, and that index space is what cross-correlates
    with the puzzle's signal. Doing the textbook-correct `convert("L")`
    destroys the match (mean luminance ~142, scores ~0.2 → 0% acceptance);
    leaving palette indices alone gives mean ~44 and 80% acceptance. See
    docs/PHASE_3_2.md spike notes.

    RGBA puzzles get Bobsilvio's exact OpenCV path replicated: the
    BGR2GRAY weight swap means we apply gray = 0.114R + 0.587G + 0.299B
    (note R/B are swapped vs the standard ITU-R 601 weights — this is a
    deliberate cv2 quirk we mirror so scores stay bit-exact with the
    reference implementation)."""
    raw = base64.b64decode(b64)
    pil = Image.open(BytesIO(raw))
    arr = np.array(pil)
    if arr.ndim == 2:
        return arr.astype(np.uint8)
    if arr.shape[2] == 4:
        rgb = arr[..., :3].astype(np.float32)
        gray = 0.114 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.299 * rgb[..., 2]
        return gray.astype(np.uint8)
    if arr.shape[2] == 3:
        rgb = arr.astype(np.float32)
        gray = 0.114 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.299 * rgb[..., 2]
        return gray.astype(np.uint8)
    raise ValueError(f"Unexpected captcha image shape: {arr.shape}")


def _encrypt_point(x: float, y: float, secret_key: str) -> str:
    """AES-ECB(secret_key) of `{"x":<x>,"y":<y>}` JSON, base64-encoded.
    This is the pointJson sent to /captcha/check.

    `pycryptodome` is imported lazily so a missing dep only blows up when
    a login is actually attempted (not on module import / passive sensor
    polling)."""
    body = json.dumps({"x": float(x), "y": float(y)}, separators=(",", ":")).encode("utf-8")
    return _aes_ecb_base64(body, secret_key)


def _build_captcha_verification(
    token: str, x: float, y: float, secret_key: str
) -> str:
    """Construct the `captchaVerification` string passed to /login.

    Crucially this is NOT `token + "---" + pointJson` (that was my first
    incorrect guess and the cube responds "captcha is overdue"). The real
    format is:
        AES_ECB(secret_key, f"{token}---{json({x,y})}")  base64-encoded
    i.e. AES-encrypt the whole literal `token---plaintext_json` string,
    not just the {x,y} point. Same `secret_key` as used for pointJson.
    Source: Bobsilvio/epcube-token app.py `generate_captcha_verification`,
    confirmed against the live cube 2026-05-21."""
    json_point = json.dumps({"x": float(x), "y": float(y)}, separators=(",", ":"))
    raw = f"{token}---{json_point}".encode("utf-8")
    return _aes_ecb_base64(raw, secret_key)


def _aes_ecb_base64(plaintext: bytes, key: str) -> str:
    """AES-ECB encrypt with PKCS7 padding, base64-encoded output."""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    cipher = AES.new(key.encode("utf-8"), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(plaintext, AES.block_size))).decode("utf-8")


# ----------------------------------------------------------------------
# Network plumbing — open endpoints (no Bearer required)
# ----------------------------------------------------------------------
async def _fetch_captcha(
    session: aiohttp.ClientSession, base: str, client_uid: str
) -> dict[str, Any]:
    body = await _post_json(
        session,
        f"{base}/open/common/captcha/get",
        {"clientUid": client_uid},
    )
    rep = _unwrap(body)
    rep_data = rep.get("repData") or rep
    for key in ("originalImageBase64", "jigsawImageBase64", "secretKey", "token"):
        if key not in rep_data:
            raise CaptchaSolveError(
                f"captcha/get missing field {key!r}: {_shortest_message(body)}"
            )
    return rep_data


def _check_succeeded(check_response: dict[str, Any]) -> bool:
    """Did /api/open/common/captcha/check accept our solution?

    Important: the outer envelope `message` is `"Success"` even when the
    captcha verification failed — that just means the HTTP envelope was OK.
    The real success indicator is at the inner aj-captcha layer:
      - `data.repData.result == true` (aj-captcha convention)
      - `data.repCode == "0000"` (this firmware's idiom — failure is "6111"
        with `repMsg` "验证失败" / verification failed; observed 2026-05-21)
    Do NOT add a `message == "Success"` fallback — that's exactly the bug
    that lets a failed captcha proceed to /login and trigger the misleading
    "captcha is overdue" error."""
    if not isinstance(check_response, dict):
        return False
    data = check_response.get("data")
    if not isinstance(data, dict):
        return False
    rep_data = data.get("repData")
    if isinstance(rep_data, dict) and rep_data.get("result") is True:
        return True
    return data.get("repCode") == "0000"


async def _do_login(
    session: aiohttp.ClientSession,
    base: str,
    *,
    username: str,
    password: str,
    verification: str,
) -> str:
    """POST /api/open/common/login. Returns the Bearer token."""
    body = await _post_json(
        session,
        f"{base}/open/common/login",
        {
            "userName": username,
            "password": password,
            "captchaVerification": verification,
        },
    )
    data = _unwrap(body)
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise LoginError(f"login response missing token: {_shortest_message(body)}")
    return str(token)


async def _post_json(
    session: aiohttp.ClientSession, url: str, payload: dict[str, Any]
) -> dict[str, Any]:
    async with session.post(url, headers=_HEADERS, json=payload) as resp:
        if resp.status >= 500:
            raise LoginError(f"{url} returned {resp.status}: {(await resp.text())[:300]}")
        try:
            return await resp.json()
        except aiohttp.ContentTypeError as err:
            raise LoginError(f"{url} returned non-JSON: {err}") from err


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    """Return body['data'] if it's a dict, else body. The cloud wraps
    successful responses in `{status, message, data: {...}}`."""
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else (body or {})


def _shortest_message(body: dict[str, Any]) -> str:
    """Best-effort message extractor for error logging."""
    if not isinstance(body, dict):
        return str(body)[:200]
    for key in ("message", "msg"):
        v = body.get(key)
        if v:
            return str(v)
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("repMsg", "message", "msg"):
            v = data.get(key)
            if v:
                return str(v)
    return json.dumps(body)[:200]
