#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 https://downloads.example.com [data-directory]" >&2
  exit 2
fi

BASE_URL=${1%/}
DATA_DIR=${2:-data}
DOWNLOAD_DIR="$DATA_DIR/downloads"

command -v curl >/dev/null 2>&1 || {
  echo "curl is required" >&2
  exit 1
}
command -v zstd >/dev/null 2>&1 || {
  echo "zstd is required (Ubuntu: sudo apt install zstd)" >&2
  exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
  echo "sha256sum is required" >&2
  exit 1
}

mkdir -p "$DOWNLOAD_DIR"
curl --fail --location --retry 3 \
  "$BASE_URL/SHA256SUMS" -o "$DOWNLOAD_DIR/SHA256SUMS"

for name in users.db.zst threads.db.zst; do
  echo "Downloading $name..."
  curl --fail --location --retry 3 --continue-at - \
    "$BASE_URL/$name" -o "$DOWNLOAD_DIR/$name"
done

(
  cd "$DOWNLOAD_DIR"
  sha256sum --check SHA256SUMS
)

zstd -d -f "$DOWNLOAD_DIR/users.db.zst" -o "$DATA_DIR/users.db"
zstd -d -f "$DOWNLOAD_DIR/threads.db.zst" -o "$DATA_DIR/threads.db"
echo "Databases are ready in $DATA_DIR"
