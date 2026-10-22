#!/usr/bin/env bash
# Pipeline 2 (Backend threejs) – Setup: three.js-Paket nach third_party/three holen.
# Aufruf aus dem Repo-Root:  bash scripts/setup_threejs.sh
# Kein Node/npm nötig, das Paket kommt als Tarball direkt aus der npm-Registry.
set -euo pipefail

VERSION="${THREE_VERSION:-0.180.0}"   # gepinnt; am besten auf die Version im VUSE-Hub setzen
TARGET="third_party/three"

if [ -f "$TARGET/package.json" ] && grep -q "\"version\": \"$VERSION\"" "$TARGET/package.json"; then
  echo "three.js $VERSION ist schon in $TARGET"
else
  rm -rf "$TARGET"
  mkdir -p "$TARGET"
  curl -fsSL "https://registry.npmjs.org/three/-/three-$VERSION.tgz" | tar -xz -C "$TARGET" --strip-components=1
  echo "three.js $VERSION -> $TARGET"
fi

# Chromium für Playwright (headless Browser, in dem three.js rendert)
uv run playwright install chromium
