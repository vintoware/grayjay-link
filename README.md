# Grayjay Link

Open YouTube, Twitch and other video links in [Grayjay Desktop](https://grayjay.app) instead of a browser tab, on KDE Plasma.

Click a YouTube link in Firefox and the video starts in Grayjay. If Grayjay isn't running it is started, and its window is brought to the front.

It works with the **unmodified Flathub Grayjay**. Grayjay Desktop has no way to accept a URL from outside: no command-line argument, no URL scheme, and its local HTTP API is token-protected. The one way in is the **sync protocol**, which the phone app uses for "Send to device". Grayjay Link pairs with Grayjay as if it were another device and sends links that way.

```
Firefox ──native messaging──┐
grayjay-link: URLs ─────────┼─► grayjay-link daemon ──sync protocol (127.0.0.1:12315)──► Grayjay
Plasma widget / CLI ────────┘   (runs while the widget exists)   "Send to device" (opcode 101)
```

## Components

| Path | What it is |
|---|---|
| `grayjay_link/` | Python helper: sync-protocol client, background daemon and CLI. Pure Python 3.11+, no extra packages. |
| `plasmoid/` | Plasma 6 widget: status, pairing, sending a link, unpairing. It also owns the daemon's lifetime. |
| `firefox/` | Firefox extension: right-click **Open link in Grayjay**, a toolbar button, and optional click interception. |
| `data/` | `grayjay-link:` URL handler and native messaging manifest templates. |

## Install

```sh
./install.sh
```

Everything is installed for the current user, with no root needed:
- the helper goes in `~/.local/share/grayjay-link` and `~/.local/bin/grayjay-link`;
- the Plasma widget, the icon and the `grayjay-link:` handler are registered;
- the Firefox native messaging host is registered. Firefox only reads per-user manifests from `~/.mozilla`, and on
  XDG-layout installs (profiles in `~/.config/mozilla/firefox`, as on current Fedora) that directory must not be
  created: if it exists, Firefox switches back to the legacy profile location and your profiles seem to vanish.
  In that case the installer asks for `sudo` once and installs `/usr/lib64/mozilla/native-messaging-hosts/grayjay_link.json`
  plus a small wrapper, `/usr/local/libexec/grayjay-link-native-host`, which runs the calling user's helper.

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

Release builds of Firefox only install extensions that Mozilla has signed. Sign this one once as an **unlisted** add-on. It's free, and the add-on is never published on addons.mozilla.org.

1. Build the zip: `tools/package-firefox.sh` creates `dist/grayjay-link-firefox-<version>.zip`.
2. Sign in at the [AMO Developer Hub](https://addons.mozilla.org/developers/) and choose **Submit a New Add-on** → **On your own**. Upload the zip. AMO validates it and signs it automatically, usually within minutes.
3. Download the signed `.xpi` from the add-on's version page. Open it in Firefox (drag it onto a window, or *File → Open File*) and confirm **Add**.

For a new version, bump `version` in `firefox/manifest.json`, then rebuild and upload it under the same add-on with **Upload New Version**.

To just try it, open `about:debugging` → *This Firefox* → *Load Temporary Add-on…* and pick `firefox/manifest.json`. It stays loaded until Firefox restarts.

By default the extension adds:
- **Open link in Grayjay** and **Open page in Grayjay** to the right-click menu;
- a toolbar button that opens the current page.

To make plain clicks open in Grayjay, enable *Open supported links in Grayjay when I click them* in the extension's options. Firefox then asks for permission to run on all sites. Interception only applies to links that lead to *another* site, so browsing around youtube.com itself is unaffected. Ctrl/middle click still opens a tab.

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
- The control socket (`$XDG_RUNTIME_DIR/grayjay-link/control.sock`) is only accessible to your user.
- The browser can only ask the helper to `open` a URL or report `status`. It cannot pair or unpair.

## Uninstall

```sh
./uninstall.sh            # keeps the pairing key
./uninstall.sh --purge    # also forgets the key
```

Remove the *Grayjay Link* device under **Sync** in Grayjay afterwards. Otherwise Grayjay keeps retrying it.

## Credits

The icon is Grayjay's app icon (© FUTO) with a sync badge added. Grayjay and FUTO are trademarks of FUTO Holdings, Inc. This project is not affiliated with FUTO.

Licensed under the MIT License (see `LICENSE`). The license covers this project's own code only.
