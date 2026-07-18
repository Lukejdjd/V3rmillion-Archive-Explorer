#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_dir="${1:-$script_dir/../data}"
output_dir="${2:-$data_dir/downloads}"

command -v zstd >/dev/null 2>&1 || {
  echo "zstd is required. Install it, then run this script again." >&2
  exit 1
}

mkdir -p -- "$output_dir"

for name in users.db threads.db; do
  source_path="$data_dir/$name"
  target_path="$output_dir/$name.zst"
  [[ -f "$source_path" ]] || {
    echo "Missing database: $source_path" >&2
    exit 1
  }

  echo "Compressing $name with Zstandard level 15..."
  zstd -15 -T0 -f -o "$target_path" -- "$source_path"
  zstd -t -- "$target_path"
done

(
  cd -- "$output_dir"
  sha256sum users.db.zst threads.db.zst > SHA256SUMS
  sha256sum -c SHA256SUMS
)

echo "Created and verified both database packages in $output_dir"
ls -lh -- "$output_dir/users.db.zst" "$output_dir/threads.db.zst"
