#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE="${TCODE_COMPUTER_DRIVER_ARCHIVE:-$DESKTOP_DIR/vendor/cua-driver-rs-0.35.2-darwin-arm64.tar.gz}"
EXPECTED="4922ddd042d3b2197a9c034926472c1d6e0c3daa55fff7f146beb720948cbdd7"
URL="https://github.com/trycua/cua/releases/download/cua-driver-rs-v0.35.2/cua-driver-rs-0.35.2-darwin-arm64.tar.gz"
mkdir -p "$DESKTOP_DIR/vendor"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
DOWNLOADED=0
if [ ! -f "$ARCHIVE" ]; then
  if [ -n "${TCODE_COMPUTER_DRIVER_ARCHIVE:-}" ]; then
    echo "Cua Driver archive not found: $ARCHIVE" >&2
    exit 1
  fi
  ARCHIVE="$TMP_DIR/driver.tar.gz"
  curl -fL --retry 2 --connect-timeout 10 --max-time 90 -o "$ARCHIVE" "$URL"
  DOWNLOADED=1
fi
ACTUAL="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "Cua Driver archive checksum mismatch: $ARCHIVE" >&2
  exit 1
fi
tar -xzf "$ARCHIVE" -C "$TMP_DIR" --strip-components=1
BIN_HASH="$(shasum -a 256 "$TMP_DIR/cua-driver" | awk '{print $1}')"
if [ "$BIN_HASH" != "512f488f631469decfebecc6e5f54e74a6f10e78f9644f89f1eb43ce7e8426ba" ]; then
  echo "Cua Driver binary checksum mismatch" >&2
  exit 1
fi
install -m 0755 "$TMP_DIR/cua-driver" "$DESKTOP_DIR/vendor/cua-driver"
install -m 0644 "$TMP_DIR/LICENSE" "$DESKTOP_DIR/vendor/CUA_DRIVER_LICENSE"
install -m 0644 "$TMP_DIR/THIRD_PARTY_NOTICES.md" "$DESKTOP_DIR/vendor/CUA_DRIVER_THIRD_PARTY_NOTICES.md"
if [ "$DOWNLOADED" = 1 ]; then
  mv "$ARCHIVE" "$DESKTOP_DIR/vendor/cua-driver-rs-0.35.2-darwin-arm64.tar.gz"
fi
