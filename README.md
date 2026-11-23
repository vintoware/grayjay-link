# Grayjay Link

Open YouTube, Twitch and other video links in [Grayjay Desktop](https://grayjay.app) instead of a browser tab, on KDE Plasma.

Click a YouTube link in Firefox and the video starts in Grayjay. If Grayjay isn't running it is started, and its window is brought to the front.

It works with the **unmodified Flathub Grayjay**. Grayjay Desktop has no way to accept a URL from outside: no command-line argument, no URL scheme, and its local HTTP API is token-protected. The one way in is the **sync protocol**, which the phone app uses for "Send to device". Grayjay Link pairs with Grayjay as if it were another device and sends links that way.

```
Firefox ──http://127.0.0.1───┐
grayjay-link: URLs ─────────┼─► grayjay-link daemon ──sync protocol (127.0.0.1:12315)──► Grayjay
Plasma widget / CLI ────────┘   (runs while the widget exists)   "Send to device" (opcode 101)
```

## Components

| Path | What it is |
|---|---|
| `grayjay_link/` | Python helper: sync-protocol client, background daemon and CLI. Pure Python 3.11+, no extra packages. |
| `plasmoid/` | Plasma 6 widget: status, pairing, sending a link, unpairing. It also owns the daemon's lifetime. |
| `firefox/` | Firefox extension: right-click **Open link in Grayjay**, a toolbar button, and optional click interception. |
| `data/` | `grayjay-link:` URL handler template. |

## Install

### From the vintoware dnf repository (Fedora)

```sh
sudo dnf config-manager addrepo --from-repofile=https://vintoware.github.io/vintoware-rpm/vintoware.repo
sudo dnf install grayjay-link
```

This installs the helper (`/usr/bin/grayjay-link`), the Plasma widget, the icon and the
`grayjay-link:` handler system-wide. New releases arrive with `sudo dnf upgrade`.
Then add the widget and pair, as described below. If you used `install.sh` before, run
`./uninstall.sh` first; your pairing in `~/.config/grayjay-link` is kept.

### From a checkout

```sh
./install.sh
```

Everything is installed for the current user, with no root needed:
- the helper goes in `~/.local/share/grayjay-link` and `~/.local/bin/grayjay-link`;
- the Plasma widget, the icon and the `grayjay-link:` handler are registered;
- nothing outside your home directory: no root, no system files. The Firefox extension talks to the daemon over a loopback HTTP endpoint (see *Security notes*).

Requirements:
- Grayjay from Flathub (`app.grayjay.Grayjay`);
- under **Sync** in Grayjay: Sync enabled, local connections on, and *pair through relay* on (all are the defaults).

### Pair

1. Add the **Grayjay Link** widget to a panel (right-click the panel → *Add Widgets…*).
2. In Grayjay, open **Sync** and copy the pairing link (the copy button beside the QR code).
3. Paste it into the widget and press **Pair**.
4. Accept the **Pairing Request** in Grayjay.

Or from a terminal: `grayjay-link pair 'grayjay://sync/…'`.

### Firefox extension

Each release has a Mozilla-signed `grayjay-link-firefox-X.Y.Z.xpi` attached. It's signed as an
*unlisted* add-on, so it never appears on addons.mozilla.org. Open the `.xpi` from the
[latest release](https://github.com/vintoware/grayjay-link/releases/latest) in Firefox
(drag it onto a window, or *File → Open File*) and confirm **Add**.

From 0.3.0 on, Firefox updates the extension by itself. The manifest's `update_url` points
at the `updates.json` on the latest release, and Firefox checks it about once a day; you can
also use *about:addons → ⚙ → Check for Updates*. Versions installed before 0.3.0 don't know
that URL, so install 0.3.0 by hand once.

To try a local change, open `about:debugging` → *This Firefox* → *Load Temporary Add-on…*
and pick `firefox/manifest.json`. It stays loaded until Firefox restarts.

By default the extension adds:
- **Open link in Grayjay** and **Open page in Grayjay** to the right-click menu;
- a toolbar button that opens the current page.

To make plain clicks open in Grayjay, enable *Open supported links in Grayjay when I click them* in the extension's options. Firefox then asks for permission to run on all sites. Interception only applies to links that lead to *another* site, so browsing around youtube.com itself is unaffected. Ctrl/middle click still opens a tab.

### Links from other apps

The Firefox extension only sees links you click *inside* Firefox. Links that other apps open
(Slack, Konsole, mail, Flatpak apps through the desktop portal) go to the default web browser
instead. To open those in Grayjay as well, turn on **Links from other apps** in the widget, or
run `grayjay-link link-handler on`.

This makes `grayjay-link-browser.desktop` the handler for `http`/`https` links:

- a supported link (YouTube videos, shorts, live streams, playlists and channels; Twitch
  channels and VODs) opens in Grayjay when the helper is running;
- every other link, and every link while the helper isn't running, goes straight to the
  browser that was the default before, so nothing else changes.

Local `.html` files are left to the browser. Turning the switch off (or running `uninstall.sh`)
restores the previous default browser. Limitations: Firefox may report that it isn't the default
browser any more; and links Grayjay itself opens "in the browser" are sent back to Grayjay if
they're supported.

### Other apps

Any program can hand a link over with:

```sh
xdg-open 'grayjay-link://open?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DdQw4w9WgXcQ&t=30'
xdg-open 'grayjay-link:https://youtu.be/dQw4w9WgXcQ'
grayjay-link open https://www.twitch.tv/somechannel
```

## Daemon lifetime

The daemon runs exactly as long as the widget does. You don't need a system service.

- While the widget exists (on a panel or on the desktop, expanded or not), it polls the daemon every few seconds. Each poll counts as a heartbeat, and the widget starts the daemon if it isn't running.
- When heartbeats stop for 30 s, the daemon exits. That happens when you remove the widget, when plasmashell quits or crashes, or when you log out.
- Firefox, `grayjay-link:` links and the CLI use the daemon while it's running. They never start it or keep it alive. Without the widget they report *Grayjay Link isn't running*.

> **Note:** while Grayjay is running and the daemon isn't (for example after you remove the widget but stay paired), Grayjay retries the helper every 5 seconds. Each retry writes two errors to Grayjay's `log.txt`; see *Why a daemon* below. To stop that, unpair before removing the widget, or remove the *Grayjay Link* device under **Sync** in Grayjay.

## CLI

```
grayjay-link status [--json]     connection state
grayjay-link pair LINK           pair using Grayjay's grayjay://sync/… link
grayjay-link open URL [--position SECONDS] [--no-launch] [--no-focus]
grayjay-link unpair
grayjay-link link-handler on|off|status   open links from other apps in Grayjay
grayjay-link browse [--dry-run] URL...     route links like a browser (what the dispatcher runs)
grayjay-link start | stop        start/stop the background daemon (the widget starts it)
grayjay-link daemon [-v] [--lease S]   run it in the foreground
```

Daemon log: `~/.local/state/grayjay-link/daemon.log`.

## How it works

The protocol was reimplemented from Grayjay's source-available code: `Grayjay.Desktop` and FUTO's `syncserver` at commit `57f8cd78`, the revision pinned by the current Flathub build.

- **Transport.** Each side first sends a protocol version (5). Then comes a Noise handshake, `Noise_IK_25519_ChaChaPoly_BLAKE2b`, with app id `GRJS`. After that, every frame is a ChaChaPoly-encrypted packet: `int32 size | opcode | subOpcode | encoding | data`.
- **Pairing.** The pairing code is sealed with `Noise_N_25519_ChaChaPoly_BLAKE2b` to Grayjay's static key. Pairing has to go **through FUTO's relay** (`relay.grayjay.app:9000`), as the phone app's does. On *direct* connections, Grayjay's responder reads the pairing message from the wrong offset (`HandshakeAsResponderAsync`), so direct pairing with a code can never succeed. Pairing doesn't work the other way round either: Grayjay's *Add device* tries the relay first and gives up when it fails. The relay only forwards encrypted packets. After pairing, the helper's key is trusted and all connections are direct on `127.0.0.1`.
- **Opening a link.** The daemon sends `DATA / 101 (SendToDevice)` with `{"url": …, "position": …}`. Grayjay passes this to its UI's `OpenUrl` handler, which opens the video, channel or playlist. Grayjay drops the event if its UI hasn't loaded yet, so when the daemon has just launched Grayjay it waits for the UI first.
- **Why a daemon.** Grayjay retries every paired device that isn't connected every 5 seconds, using the device's last address. For this helper that address is `127.0.0.1:12315`, which is Grayjay itself, so each retry would log two errors. Keeping one session open avoids that. The session idles with pings every 5 s, and the daemon ignores the sync data Grayjay pushes (subscriptions, history and so on).
- **Raising the window** uses a one-off KWin script over D-Bus, because Wayland focus-stealing prevention would otherwise keep Grayjay in the background.

Crypto (X25519, ChaCha20-Poly1305) is implemented in pure Python. BLAKE2b comes from `hashlib`. It is tested against the RFC 7748 and RFC 8439 vectors and the Noise test vectors for both patterns (`tests/`). Run the tests with:

```sh
python3 -m unittest
```

## Security notes

- The private key lives in `~/.config/grayjay-link/state.json` with mode 0600. Anyone holding it can send links to your Grayjay, and receives the sync data Grayjay pushes to paired devices.
- The control socket (`$XDG_RUNTIME_DIR/grayjay-link/control.sock`) is only accessible to your user. Pairing and unpairing are only possible through it.
- The browser endpoint `http://127.0.0.1:47315` (`http_port` in `state.json`; must match the extension's option) only offers `open` and `status`. It is written so that web pages can't use it:
  - every request must carry an `X-Grayjay-Link: 1` header. Forms can't set one, and a page's `fetch()` that sets one needs a CORS preflight, which is always refused;
  - a request that has an `Origin` header is accepted only from `moz-extension://`, which web content can't forge;
  - the `Host` header must be `127.0.0.1` or `localhost` on that port, so DNS rebinding is blocked;
  - no CORS headers are ever sent, so web pages can't read responses.

  Other extensions in your Firefox, and local programs running as you, can open links in Grayjay through it, the same as they could through `xdg-open grayjay-link:…`.

## Releases

Versions follow Semantic Versioning, and commit messages follow Conventional Commits. A release
is made by pushing the commit that `tools/release.py prepare` creates. CI then tags it,
publishes the GitHub release with a generated changelog (Features / Fixes / Chores), and
triggers the RPM build in vintoware-rpm. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[CHANGELOG.md](CHANGELOG.md).

## Uninstall

```sh
./uninstall.sh            # keeps the pairing key
./uninstall.sh --purge    # also forgets the key
```

Remove the *Grayjay Link* device under **Sync** in Grayjay afterwards. Otherwise Grayjay keeps retrying it.

## Credits

The icon is Grayjay's app icon (© FUTO) with a sync badge added. Grayjay and FUTO are trademarks of FUTO Holdings, Inc. This project is not affiliated with FUTO.

Licensed under the MIT License (see `LICENSE`). The license covers this project's own code only.
