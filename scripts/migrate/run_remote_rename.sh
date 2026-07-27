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
