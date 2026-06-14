#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# scribe.sh — Download a YouTube video, transcribe it, and push to GitHub
#
# Usage:
#   scribe.sh <youtube_url> [output_filename]
#
# output_filename: name for the .txt file (no path, no extension needed).
#   Defaults to a sanitised version of the video title.
#   Stored at transcripts/<output_filename>.txt in SCRIBE_REPO.
# ---------------------------------------------------------------------------

YOUTUBE_URL="${1:?Error: YouTube URL required.  Usage: scribe.sh <url> [output_filename]}"
SCRIPT_DIR="${SCRIBE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO="${SCRIBE_REPO:-pranavgupta55/Scribe}"

# Resolve output filename
if [ -n "${2:-}" ]; then
  FILENAME="${2%.txt}.txt"
else
  echo "🔍 Fetching video title..."
  RAW_TITLE=$(yt-dlp --print title "$YOUTUBE_URL" 2>/dev/null)
  FILENAME=$(echo "$RAW_TITLE" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/_/g' \
    | sed 's/__*/_/g' \
    | sed 's/^_//;s/_$//').txt
  echo "    → ${FILENAME}"
fi

TMPDIR_PATH=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PATH"' EXIT

echo "⬇️  Downloading audio..."
yt-dlp \
  --extract-audio \
  --audio-format mp3 \
  --audio-quality 0 \
  --output "$TMPDIR_PATH/audio.%(ext)s" \
  --quiet --progress \
  "$YOUTUBE_URL"

echo "🤖 Transcribing..."
python3 "${SCRIPT_DIR}/qwen_transcribe.py" \
  "$TMPDIR_PATH/audio.mp3" \
  "$TMPDIR_PATH/${FILENAME}"

# Upload to GitHub via API (creates or overwrites the file)
echo "☁️  Uploading to github.com/${REPO}/transcripts/${FILENAME}..."
CONTENT=$(base64 < "$TMPDIR_PATH/${FILENAME}" | tr -d '\n')
REMOTE_PATH="transcripts/${FILENAME}"

# Fetch existing file SHA so we can overwrite if it already exists
EXISTING_SHA=$(gh api "repos/${REPO}/contents/${REMOTE_PATH}" \
  --jq '.sha' 2>/dev/null || echo "")

if [ -n "$EXISTING_SHA" ]; then
  gh api --method PUT "repos/${REPO}/contents/${REMOTE_PATH}" \
    -f message="transcript: ${FILENAME}" \
    -f content="$CONTENT" \
    -f sha="$EXISTING_SHA" \
    --silent
else
  gh api --method PUT "repos/${REPO}/contents/${REMOTE_PATH}" \
    -f message="transcript: ${FILENAME}" \
    -f content="$CONTENT" \
    --silent
fi

echo "✅ Done → github.com/${REPO}/blob/main/${REMOTE_PATH}"
