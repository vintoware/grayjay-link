#!/usr/bin/env bash
# Build dist/grayjay-link-firefox-<version>.zip for signing on addons.mozilla.org.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/firefox/manifest.json")"
OUT="$ROOT/dist/grayjay-link-firefox-$VERSION.zip"
mkdir -p "$ROOT/dist"
rm -f "$OUT"
python3 - "$ROOT/firefox" "$OUT" <<'PY'
import pathlib, sys, zipfile
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.rglob("*")):
        if f.is_file():
            z.write(f, f.relative_to(src).as_posix())  # manifest.json at the zip root
PY
echo "$OUT"
