"""End-to-end: real EPCubeClient -> HTTP -> real mock_server -> real PredbatShim.

The shim unit tests (test_shim.py, test_shim_dynamic.py) use a fake client with
no HTTP, so they never exercise the wire format or the mock server's modelling
of the cube. These tests start the actual mock server on a loopback port and
drive the Dynamic-tariff paths through the real client.

Skipped when the mock server's dependencies (fastapi, uvicorn, Pillow) are not
installed, so the rest of the suite is unaffected.
"""
from __future__ import annotations

import socket
import threading
import time
from datetime import timedelta

import aiohttp
import pytest
from homeassistant.util import dt as dt_util

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("PIL")

import uvicorn  # noqa: E402

from custom_components.ep_cube.api import (  # noqa: E402
    EPCubeClient,
    EPCubeError,
    payload_from_switch_mode_read,
)
from custom_components.ep_cube.const import FORCE_EXPORT_MODE_DYNAMIC  # noqa: E402
from custom_components.ep_cube.services import PredbatShim  # noqa: E402
from mock_server.main import app  # noqa: E402

TOKEN = "mock-token"
TIER_KEYS = (
    "offPeakTimeList", "midPeakTimeList", "peakTimeList",
    "offPeakTimeListNonWorkDay", "midPeakTimeListNonWorkDay", "peakTimeListNonWorkDay",
)


def _future(minutes: int):
    return dt_util.utcnow() + timedelta(minutes=minutes)


def _tiers(state):
    return {k: state.get(k) for k in TIER_KEYS}


@pytest.fixture
def mock_base(socket_enabled):
    """Run the mock server in a thread on a free loopback port."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "mock server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


async def _sim(session, base, dev_id, path, body=None):
    async with session.post(f"{base}/__sim__/device/{dev_id}/{path}", json=body or {}) as resp:
        assert resp.status == 200, await resp.text()


@pytest.fixture
async def rig(hass, mock_base):
    """A client wired to the mock, a cube seeded in TOU with a Go-like schedule,
    and a factory for shims (cancelled at teardown)."""
    async with aiohttp.ClientSession() as session:
        probe = EPCubeClient(session, base_url=mock_base, dev_id="0", sg_sn="0", bearer_token=TOKEN)
        device = (await probe.get_device_list())[0]
        dev_id, sg_sn = str(device["id"]), str(device["sgSn"])
        client = EPCubeClient(
            session, base_url=mock_base, dev_id=dev_id, sg_sn=sg_sn,
            bearer_token=TOKEN, capacity_kwh=20.0,
        )
        await _sim(session, mock_base, dev_id, "reset")
        await _sim(session, mock_base, dev_id, "state", {
            "workStatus": "2", "touType": 0,
            "selfConsumptioinReserveSoc": 20, "backupPowerReserveSoc": 100,
        })
        day = {
            "offPeak": [["00:30", "05:30", 0.095]],
            "midPeak": [["05:30", "16:00", 0.36], ["19:00", "23:59", 0.36]],
            "peak": [["16:00", "19:00", 0.36]],
        }
        await _sim(session, mock_base, dev_id, "tou-set-slots", {"weekday": day, "weekend": day})
        shims: list[PredbatShim] = []

        def make(mode=None) -> PredbatShim:
            kwargs = {"force_export_mode": mode} if mode else {}
            shim = PredbatShim(hass=hass, entry_id="e2e", client=client, **kwargs)
            shims.append(shim)
            return shim

        yield client, session, mock_base, dev_id, make
        for shim in shims:
            shim.cancel_revert_timer()


async def test_dynamic_export_round_trip(rig):
    client, _session, _base, _dev, make = rig
    shim = make(FORCE_EXPORT_MODE_DYNAMIC)
    base = await client.get_switch_mode()
    assert str(base["workStatus"]) == "2" and int(base["touType"]) == 0

    await shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
    during = await client.get_switch_mode()
    assert str(during["workStatus"]) == "2" and int(during["touType"]) == 1
    assert float(during["dynamicDischargePrice"]) == 5.0
    assert float(during["dynamicChargePrice"]) == 0.0
    assert int(during["selfConsumptioinReserveSoc"]) == 50
    assert _tiers(during) == _tiers(base), "a Dynamic write must not change the TOU lists"

    await shim._on_revert_timer(dt_util.utcnow())          # window end
    back = await client.get_switch_mode()
    assert int(back["touType"]) == 0 and str(back["workStatus"]) == "2"
    assert int(back["selfConsumptioinReserveSoc"]) == int(base["selfConsumptioinReserveSoc"])
    assert int(back["backupPowerReserveSoc"]) == int(base["backupPowerReserveSoc"])
    assert _tiers(back) == _tiers(base), "TOU schedule not restored to the baseline"
    assert shim._active_override is None

    await shim._on_watchdog(dt_util.utcnow())              # clean revert: a no-op
    again = await client.get_switch_mode()
    assert int(again["touType"]) == 0 and _tiers(again) == _tiers(base)


async def test_tou_override_and_restore_while_cube_is_in_users_dynamic_mode(rig):
    """Issue #1: a cube already in Dynamic mode must still take a TOU override,
    and a revert must give the user their Dynamic mode back."""
    client, session, base, dev_id, make = rig
    await _sim(session, base, dev_id, "state", {
        "touType": 1, "dynamicChargePrice": 12.0, "dynamicDischargePrice": 20.0})
    live = await client.get_switch_mode()
    assert int(live["touType"]) == 1

    # The mock models the cloud rejecting a TOU slot EDIT that still says
    # touType 1 ...
    bad = payload_from_switch_mode_read(dev_id, live)
    bad["offPeakTimeList"] = [*(bad.get("offPeakTimeList") or []), "10:00_10:30_2.22"]
    with pytest.raises(EPCubeError):
        await client.switch_mode(bad)
    # ... but a touType 1 write that leaves the lists alone is accepted (this is
    # what a real cube in Dynamic mode holds, and what the shim's export writes).
    await client.switch_mode(payload_from_switch_mode_read(dev_id, live))

    shim = make()   # default tou_peak mode
    await shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)
    during = await client.get_switch_mode()
    assert str(during["workStatus"]) == "2" and int(during["touType"]) == 0

    await shim.discharge_stop()
    after = await client.get_switch_mode()
    assert int(after["touType"]) == 1, "the user's Dynamic mode should be restored"
    assert float(after["dynamicChargePrice"]) == 12.0
    assert float(after["dynamicDischargePrice"]) == 20.0
    assert _tiers(after) == _tiers(live), "TOU lists changed by an override + revert"


async def test_failed_revert_is_recovered_by_retry_and_watchdog(rig):
    client, _session, _base, _dev, make = rig
    shim = make(FORCE_EXPORT_MODE_DYNAMIC)
    base = await client.get_switch_mode()
    await shim.discharge_start(end_time=_future(30), target_soc_pct=50.0)

    real = client.switch_mode
    pending = {"fail": 1}

    async def flaky(payload, **kwargs):
        if pending["fail"] > 0:
            pending["fail"] -= 1
            raise EPCubeError("simulated cloud outage")
        return await real(payload, **kwargs)

    client.switch_mode = flaky
    await shim._on_revert_timer(dt_util.utcnow())           # the revert fails once
    stuck = await client.get_switch_mode()
    assert int(stuck["touType"]) == 1, "the cube is still in the export's Dynamic mode"
    assert shim._active_override is not None and shim._revert_retries == 1

    await shim._on_revert_retry(dt_util.utcnow())           # the retry succeeds
    ok = await client.get_switch_mode()
    assert int(ok["touType"]) == 0 and shim._active_override is None
    assert _tiers(ok) == _tiers(base)
    await shim._on_watchdog(dt_util.utcnow())
    assert int((await client.get_switch_mode())["touType"]) == 0


async def test_stale_shim_dynamic_export_is_recovered_with_the_default_reserve(rig):
    """HA restarted mid-window: the cube is left in the shim's own Dynamic export."""
    client, session, base, dev_id, make = rig
    await _sim(session, base, dev_id, "state", {
        "workStatus": "2", "touType": 1, "dynamicChargePrice": 0.0,
        "dynamicDischargePrice": 5.0, "selfConsumptioinReserveSoc": 50})

    shim = make(FORCE_EXPORT_MODE_DYNAMIC)         # a fresh shim = HA just restarted
    await shim.discharge_start(end_time=_future(30), target_soc_pct=60.0)
    assert int(shim._baseline["touType"]) == 0, "stale Dynamic must not become the baseline"
    await shim._on_revert_timer(dt_util.utcnow())
    final = await client.get_switch_mode()
    assert int(final["touType"]) == 0, "the cube must leave Dynamic mode"
    assert int(final["selfConsumptioinReserveSoc"]) == 20, "the export's reserve must not stick"


async def test_pv_strings_read_from_the_mock(rig):
    client, session, base, dev_id, _make = rig
    await _sim(session, base, dev_id, "state", {"solarPower": 2.0})
    pv = await client.get_pv_strings()
    assert set(pv) == {1, 2, 3, 4}
    assert pv[1]["power_w"] == 1200.0 and pv[2]["power_w"] == 800.0
    assert pv[1]["voltage_v"] == 250.0
    assert pv[1]["current_a"] == 4.8        # 1200 W / 250 V
    assert pv[3] == {"power_w": 0.0, "voltage_v": 0.0, "current_a": 0.0}
