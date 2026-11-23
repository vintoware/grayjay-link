# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org);
sections are generated from [Conventional Commits](https://www.conventionalcommits.org) by
`tools/release.py` (see CONTRIBUTING.md).

## 0.2.0 (2026-11-23)

First public release.

### Features

- Helper that pairs with Grayjay Desktop (Flathub) through Grayjay's sync protocol, as the phone app does, and opens links with "Send to device". Pairing goes once through Grayjay's relay; after that, everything stays on `127.0.0.1`.
- Starts Grayjay if it isn't running, waits for its UI, and raises its window through KWin.
- Plasma 6 widget with status, pairing, sending a link and unpairing. The background helper runs exactly as long as the widget exists.
- Firefox extension: **Open link in Grayjay** in the right-click menu, a toolbar button, and optional interception of clicks on YouTube/Twitch links from other sites. It talks to the helper over a hardened loopback HTTP endpoint.
- `grayjay-link:` URL handler and a `grayjay-link` CLI.

### Chores

- Versioning by Conventional Commits, generated changelog, and a release workflow that publishes signed release assets and triggers the RPM build in vintoware-rpm.

