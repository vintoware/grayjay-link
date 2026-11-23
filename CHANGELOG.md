# Changelog

All notable changes to this project. Versions follow [Semantic Versioning](https://semver.org);
sections are generated from [Conventional Commits](https://www.conventionalcommits.org) by
`tools/release.py` (see CONTRIBUTING.md).

## [0.3.1](https://github.com/vintoware/grayjay-link/compare/v0.3.0...v0.3.1) (2026-11-23)

### Fixes

- **widget:** size the popup for its header so the Unpair button isn't cut off ([`aa60f84`](https://github.com/vintoware/grayjay-link/commit/aa60f8404e9e6e85a385a88f8b76792177fa7f05))


## [0.3.0](https://github.com/vintoware/grayjay-link/compare/v0.2.0...v0.3.0) (2026-11-23)

### Features

- **firefox:** update automatically from GitHub releases ([`e4d5021`](https://github.com/vintoware/grayjay-link/commit/e4d5021419a79e75cdd1770173e84b424ae65dfa))

### Chores

- **release:** sign the Firefox extension on AMO and publish updates.json ([`4e7f0f0`](https://github.com/vintoware/grayjay-link/commit/4e7f0f0b0ee039fd3e3f8eb2193bcaf276d3e64b))


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

