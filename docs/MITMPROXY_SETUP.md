# mitmproxy setup — Windows laptop + iPad

> Reference for capturing HTTPS traffic from an iPad-side EP Cube mobile
> app (or any other vendor app whose web portal lacks the surface we
> need). Phase 3 captures used HAR-from-web-portal and skipped this
> entirely; first time we actually need it is Task #4 (mobile-app TOU
> "Clear" button — not exposed in the web portal).

## Why iPad and not the Pixel 10 Pro

Stock Android 15 on the Pixel ignores user-installed CAs for any app
targeting API 24+ (i.e. every modern app). Getting the mitmproxy CA
into the system trust store needs bootloader unlock + root or a Magisk
module, both of which wreck Play Integrity and turn a 15-minute capture
into a multi-hour yak-shave. iPad cert trust is a 4-tap walkthrough —
use the iPad.

## One-time setup

### 1. Install mitmproxy on Windows

```powershell
winget install mitmproxy
```

(or download the installer from <https://mitmproxy.org/>). Confirm:

```powershell
mitmproxy --version
```

Three binaries get installed: `mitmproxy` (interactive TUI),
`mitmweb` (browser UI on `http://localhost:8081`), and `mitmdump`
(headless, writes raw flows to disk). For Task #4 we want **mitmdump**
to record the session as a `.mitm` file we can replay later.

### 2. Allow mitmproxy through the Windows firewall

First launch may surface a Windows Defender Firewall prompt for the
proxy port (8080). Allow **Private networks** (your home WiFi); deny
public.

If no prompt appears, do it manually:

```powershell
New-NetFirewallRule -DisplayName "mitmproxy 8080" `
    -Direction Inbound -Protocol TCP -LocalPort 8080 `
    -Profile Private -Action Allow
```

### 3. Find the laptop's LAN IP

The iPad needs the laptop's IP on the local WiFi network (NOT
`127.0.0.1`, NOT the Tailscale IP):

```powershell
ipconfig | Select-String "IPv4"
```

Pick the one matching your home subnet (typically `192.168.x.x` or
`10.0.x.x`). Call this **`<laptop-ip>`** from here on.

### 4. First-run: generate the CA cert

```powershell
mitmdump
```

(Ctrl-C to quit immediately — we just need the first run to generate
the CA.) The CA file lands in `%USERPROFILE%\.mitmproxy\`:

- `mitmproxy-ca-cert.pem` (this is what the iPad needs)
- `mitmproxy-ca.p12`, `mitmproxy-ca.pem` (other formats)

You don't need to copy these manually — the iPad will fetch the cert
over the proxy connection via `mitm.it` (see step 7).

## Per-capture-session setup

### 5. Start the proxy with on-disk recording

```powershell
mitmdump -w <captures-private>/2026-05-22-tou-clear.mitm
```

Listens on `0.0.0.0:8080`. Leave the terminal open; every HTTPS request
the iPad makes will scroll past as a one-liner.

To watch live in a browser UI instead (handy for following along during
the session, though it doesn't save to disk):

```powershell
mitmweb -w <captures-private>/2026-05-22-tou-clear.mitm
```

Opens <http://localhost:8081> in your default browser.

### 6. Point the iPad at the proxy

On the iPad, on the **same WiFi network**:

1. Settings → Wi-Fi → tap the (i) next to the connected network.
2. Scroll down to **Configure Proxy** → **Manual**.
3. Server: `<laptop-ip>` (the one from step 3).
4. Port: `8080`.
5. Authentication: off.
6. **Save** (top right).

### 7. Install + trust the mitmproxy CA on the iPad

This is the only step that's not obvious — both halves are required or
HTTPS will fail with cert errors instead of decrypting.

1. Open **Safari** (must be Safari — third-party browsers won't
   trigger the profile-install flow).
2. Go to <http://mitm.it>. The page will show OS-specific download
   buttons; tap the **Apple** logo → **Get mitmproxy-ca-cert.pem**.
3. iOS will say "This website is trying to download a configuration
   profile" → **Allow** → **Close**.
4. Settings → **General** → **VPN & Device Management** → tap the
   downloaded "mitmproxy" profile → **Install** (top right) →
   passcode → **Install** → **Install**.
5. **Critical second step** (easy to miss): Settings → **General** →
   **About** → scroll to bottom → **Certificate Trust Settings** →
   flip the toggle next to **mitmproxy** to ON. Confirm.

After this, every HTTPS request from the iPad will decrypt cleanly in
`mitmdump`.

### 8. Smoke test

Before exercising the EP Cube app, verify the chain end-to-end. In
Safari on the iPad, load <https://httpbin.org/get>. In the mitmdump
terminal you should see a line like:

```
192.168.x.y:54321  GET  https://httpbin.org/get
                       << 200 OK application/json 285b
```

If you see TLS errors instead — the CA didn't trust correctly. Revisit
step 7.5 (the Certificate Trust Settings toggle).

If you see no traffic at all — the iPad isn't routing through the
proxy. Revisit step 6, or check Windows Firewall (step 2).

## Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| iPad shows "Cannot connect" on every site | Proxy unreachable | Wrong LAN IP, or Windows Firewall blocked. Re-check steps 2-3. |
| All sites work in Safari but the EP Cube app shows "No internet" | App has cert pinning that ignores the OS trust store | Fall back to HAR-from-web-portal at `monitoring-eu.epcube.com`. Some vendor apps pin, most don't — we hope EP Cube doesn't. |
| Some sites decrypt, others show TLS errors | Cert is installed but trust toggle is OFF | Settings → General → About → Certificate Trust Settings → toggle ON. |
| Capture is empty despite app working | mitmdump terminal not running, or wrong port | Confirm `mitmdump -w …` is the foreground process and the port matches the iPad's proxy config (default 8080). |
| Laptop IP changed (DHCP) between sessions | Router reissued IP | Either reserve a static lease in the router, or update the iPad's proxy config to match `ipconfig`. |

## Tear-down after capture

When you're done, **remove the proxy from the iPad** — otherwise every
HTTPS request goes via your laptop's mitmproxy (or fails if the laptop
is asleep):

1. Settings → Wi-Fi → (i) on the network → Configure Proxy → **Off**.

Optionally leave the mitmproxy CA installed for next time (it's
harmless when no proxy is configured). Or remove it: Settings →
General → VPN & Device Management → mitmproxy profile → Remove
Profile.

## Replaying / analysing the captured flow

`.mitm` files can be replayed in `mitmproxy` (interactive TUI) for
manual inspection:

```powershell
mitmproxy -r <captures-private>/2026-05-22-tou-clear.mitm
```

Use `f ~m POST` to filter to POST requests, `Enter` to drill into a
flow, `q` to back out. For programmatic / Claude-side analysis, the
file is parsed by the `mitmproxy.io` Python library — I'll do that
extraction when you let me know the file is in the repo.
