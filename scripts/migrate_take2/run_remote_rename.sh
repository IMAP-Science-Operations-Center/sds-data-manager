#!/usr/bin/env bash

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-west-2}"

# Source and destination S3 buckets. DST_BUCKET must differ from SRC_BUCKET.
export SRC_BUCKET="${SRC_BUCKET:-deprecated-data-archive}"
export DST_BUCKET="${DST_BUCKET:-todo}"

# Passed through to rename.py.
export SRC_PREFIX="${SRC_PREFIX:-imap/}"
export MAJOR_VERSION="${MAJOR_VERSION:-1}"
export OVERWRITE="${OVERWRITE:-0}"
export MAX_FILES="${MAX_FILES:-0}"
export MAX_WORKERS="${MAX_WORKERS:-0}"
export DRY_RUN="${DRY_RUN:-1}"

# The default instance temp dir is too small for CDF files; use the large EBS
# root volume instead.
export TMPDIR="$HOME/tmp"
mkdir -p "$TMPDIR"

# ---------------------------------------------------------------------------
# NASA CDF C library (required by spacepy.pycdf)
# ---------------------------------------------------------------------------
# spacepy talks to the NASA CDF C library, which has no pip wheel, so build it
# once from source and expose it via the standard definitions.B env script.
sudo dnf install -y git gcc gcc-gfortran make tar gzip >/dev/null

# Bump CDF_VER if the URL below 404s (see spdf.gsfc.nasa.gov/pub/software/cdf).
CDF_VER="${CDF_VER:-cdf39_1}"
CDF_PREFIX="$HOME/cdf"
if [ ! -e "$CDF_PREFIX/lib/libcdf.so" ]; then
  echo "Building NASA CDF library ($CDF_VER) ..."
  tmp_cdf="$(mktemp -d)"
  curl -LsSf \
    "https://spdf.gsfc.nasa.gov/pub/software/cdf/dist/${CDF_VER}/unix/${CDF_VER}-dist-cdf.tar.gz" \
    -o "$tmp_cdf/cdf.tar.gz"
  tar xzf "$tmp_cdf/cdf.tar.gz" -C "$tmp_cdf"
  make -C "$tmp_cdf/${CDF_VER}-dist" OS=linux ENV=gnu CURSES=no SHARED=yes all
  make -C "$tmp_cdf/${CDF_VER}-dist" INSTALLDIR="$CDF_PREFIX" install
  rm -rf "$tmp_cdf"
fi
# definitions.B (Bourne-shell flavor) exports CDF_BASE/CDF_INC/CDF_LIB and adds
# the library to LD_LIBRARY_PATH; spacepy finds libcdf via CDF_LIB. It appends
# to LD_LIBRARY_PATH/MANPATH without first defining them, which trips `set -u`,
# so relax nounset just for the source.
set +u
source "$CDF_PREFIX/bin/definitions.B"
set -u

# ---------------------------------------------------------------------------
# uv + python deps
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

uv venv --python 3.12 .venv
source .venv/bin/activate

# rename.py needs only spacepy (CDF rewrite) and boto3 (S3) - none of the
# heavier imap_processing / sds-data-manager stack that migrate.py pulls in.
uv pip install spacepy numpy boto3

uv run python rename.py
