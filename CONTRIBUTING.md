# Contributing and releasing

## Commit messages

Every commit on `main` follows [Conventional Commits](https://www.conventionalcommits.org).
CI rejects pushes that don't (`tools/release.py lint`).

```
<type>(<optional scope>)!: <subject>

<optional body>

<optional footers, e.g. BREAKING CHANGE: what breaks and how to migrate>
```

| Type | Changelog section | Version bump |
|---|---|---|
| `feat` | Features | minor |
| `fix`, `perf` | Fixes | patch |
| `chore`, `docs`, `refactor`, `test`, `build`, `ci`, `style`, `revert` | Chores | patch |
| any type with `!`, or a `BREAKING CHANGE:` footer | ⚠ Breaking changes (and its own section) | major (minor while the version is 0.x) |

The highest bump among the commits since the last release wins. Typical scopes are
`daemon`, `protocol`, `widget`, `firefox`, `cli`, `install`, `rpm`. Scopes are optional.

## Versioning

- One [Semantic Versioning](https://semver.org) number covers the whole project: the
  helper, the Plasma widget and the Firefox extension. It lives in
  `grayjay_link/__init__.py`, `plasmoid/metadata.json` and `firefox/manifest.json`. CI
  checks that the three agree. Don't edit them by hand; the release tool does it.
- Tags are `vX.Y.Z`, created only by the release workflow.

## Making a release

```sh
git switch main && git pull
tools/release.py prepare --dry-run   # preview the version and changelog
tools/release.py prepare             # bump versions, update CHANGELOG.md, commit
git push origin main
```

`prepare` computes the next version from the commits since the last tag (override it with
`--version X.Y.Z`). It prepends the generated section to `CHANGELOG.md` and creates the
release commit `chore(release): X.Y.Z`.

When that commit reaches `main`, `.github/workflows/release.yml`:

1. checks that the version files match and the tag doesn't exist yet, then runs the tests;
2. builds the release assets: the source archive `grayjay-link-X.Y.Z.tar.gz`, the widget
   package `grayjay-link-X.Y.Z.plasmoid` and the unsigned Firefox extension
   `grayjay-link-firefox-X.Y.Z.zip`;
3. writes `checksums.txt` and signs it keylessly with Sigstore (cosign). The signature is
   tied to this workflow on `main` and to the exact release commit;
4. tags the commit `vX.Y.Z` and publishes the GitHub release, using the changelog section
   as release notes;
5. triggers the RPM build: it runs
   [vintoware-rpm](https://github.com/vintoware/vintoware-rpm)'s
   `packages/grayjay-link/update` hook, which verifies the signed checksums, and pushes the
   spec bump with a deploy key (`RPM_REPO_DEPLOY_KEY` secret). That push starts
   vintoware-rpm's build-and-publish workflow. If this step fails, vintoware-rpm's 3-hourly
   check picks the release up anyway.

The Firefox extension still needs signing on addons.mozilla.org: upload the release's
`grayjay-link-firefox-X.Y.Z.zip` as a new version of the unlisted add-on.
