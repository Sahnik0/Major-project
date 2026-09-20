#!/usr/bin/env bash
set -e

# Load .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

TIMESFM_REPO=${TIMESFM_REPO:-"google/timesfm-3.0-pytorch"}
echo "=== TimesFM Model Downloader ==="
echo "Target repository: ${TIMESFM_REPO}"

if [ -z "${HF_TOKEN}" ]; then
  echo "WARNING: HF_TOKEN is not set. TimesFM 3.0 requires an authenticated Hugging Face token."
  echo "Please create a token at https://huggingface.co/settings/tokens, accept the model terms at"
  echo "https://huggingface.co/google/timesfm-3.0-pytorch, and add HF_TOKEN=... to your .env file."
fi

# Use python inside .venv to download using huggingface_hub snapshot_download
.venv/bin/python - <<EOF
import os
import sys

repo_id = os.environ.get("TIMESFM_REPO", "google/timesfm-3.0-pytorch")
token = os.environ.get("HF_TOKEN", None)

try:
    from huggingface_hub import snapshot_download
    print(f"Downloading {repo_id} to local Hugging Face cache...")
    model_path = snapshot_download(
        repo_id=repo_id,
        token=token,
        local_files_only=False,
    )
    print(f"Successfully downloaded {repo_id} to: {model_path}")
    print("Model download complete. You can now run the app with HF_HUB_OFFLINE=1.")
except Exception as e:
    err_str = str(e)
    print(f"\nERROR downloading {repo_id}: {err_str}", file=sys.stderr)
    if "401" in err_str or "403" in err_str or "GatedRepoError" in err_str:
        print("\n--> Authorization error: This model repository is gated.", file=sys.stderr)
        print("    1. Visit https://huggingface.co/google/timesfm-3.0-pytorch", file=sys.stderr)
        print("    2. Accept the non-commercial license terms.", file=sys.stderr)
        print("    3. Ensure your HF_TOKEN is set in .env.", file=sys.stderr)
    sys.exit(1)
EOF
