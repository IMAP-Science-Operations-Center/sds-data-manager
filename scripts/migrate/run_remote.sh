#!/usr/bin/env bash

set -euo pipefail

# ---------------------------------------------------------------------------
# Config (dev account 449431850278)
# ---------------------------------------------------------------------------
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-west-2}"
export S3_BUCKET="${S3_BUCKET:-sds-data-449431850278}"
export SECRET_NAME="${SECRET_NAME:-sdp-database-cred}"

# Passed through to migrate.py.
export COPY_FILES="${COPY_FILES:-0}"
export MODIFY_ROWS="${MODIFY_ROWS:-0}"

# t3 instances uses tmpdir as half of available RAM - not nearly enough for CDF files
export TMPDIR="$HOME/tmp"
mkdir -p "$TMPDIR"


# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------
sudo dnf install -y git >/dev/null
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

uv venv --python 3.12 .venv
source .venv/bin/activate

# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------
uv pip install \
  "git+https://github.com/IMAP-Science-Operations-Center/sds-data-manager.git@release_version_work" \
  "git+https://github.com/IMAP-Science-Operations-Center/imap_processing.git@new_version_work" \
  "git+https://github.com/IMAP-Science-Operations-Center/imap-data-access.git" \
  "SQLAlchemy<=3.0.0" \
  "pandas>=3.0.3,<4.0.0" \
  psycopg2-binary \
  boto3

uv run python migrate.py
