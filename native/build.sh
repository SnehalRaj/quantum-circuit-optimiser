#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cargo_bin=${LCO_CARGO:-cargo}
if ! command -v "$cargo_bin" >/dev/null 2>&1; then
    printf '%s\n' 'Rust Cargo is unavailable. Put Cargo on PATH or set LCO_CARGO.' >&2
    exit 2
fi
cargo_bin=$(command -v "$cargo_bin")
toolchain_dir=$(dirname -- "$cargo_bin")
if [ -x "$toolchain_dir/rustc" ]; then export RUSTC=$toolchain_dir/rustc; fi
if [ -x "$toolchain_dir/rustdoc" ]; then export RUSTDOC=$toolchain_dir/rustdoc; fi
if [ -n "${LCO_CARGO_HOME:-}" ]; then export CARGO_HOME=$LCO_CARGO_HOME; fi
export CARGO_TARGET_DIR=${LCO_TARGET_DIR:-$repo_dir/work/native-target}
if [ "${LCO_OFFLINE:-0}" = 1 ]; then
    "$cargo_bin" build --release --locked --offline --manifest-path "$repo_dir/native/Cargo.toml" --bins
else
    "$cargo_bin" build --release --locked --manifest-path "$repo_dir/native/Cargo.toml" --bins
fi
exec "$CARGO_TARGET_DIR/release/small-emit" --build-info
