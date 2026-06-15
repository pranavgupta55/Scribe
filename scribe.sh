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
#
# Alongside each transcript a sidecar transcripts/<name>.meta.json records the
# source URL, video length, and transcription time (used by the graph's source
# node). Set SCRIBE_NO_UPLOAD=1 to write transcript+meta into the local repo
# instead of uploading via the GitHub API (used for batch/local processing).
# ---------------------------------------------------------------------------

YOUTUBE_URL="${1:?Error: YouTube URL required.  Usage: scribe.sh <url> [output_filename]}"
SCRIPT_DIR="${SCRIBE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO="${SCRIBE_REPO:-pranavgupta55/Scribe}"

echo "🔍 Fetching video info..."
RAW_TITLE=$(yt-dlp --retries 5 --extractor-retries 5 --print title "$YOUTUBE_URL" 2>/dev/null || echo "")

# Resolve output filename
if [ -n "${2:-}" ]; then
  FILENAME="${2%.txt}.txt"
else
  FILENAME=$(echo "$RAW_TITLE" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/_/g' \
    | sed 's/__*/_/g' \
    | sed 's/^_//;s/_$//').txt
  echo "    → ${FILENAME}"
fi
BASE="${FILENAME%.txt}"

TMPDIR_PATH=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PATH"' EXIT

echo "⬇️  Downloading audio..."
yt-dlp \
  --extract-audio \
  --audio-format mp3 \
  --audio-quality 0 \
  --retries 10 --extractor-retries 5 --fragment-retries 10 --sleep-requests 1 \
  --output "$TMPDIR_PATH/audio.%(ext)s" \
  --quiet --progress \
  "$YOUTUBE_URL"

# Video length (seconds) from the downloaded audio
DURATION=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$TMPDIR_PATH/audio.mp3" 2>/dev/null || echo "0")

echo "🤖 Transcribing..."
T0=$(date +%s)
python3 "${SCRIPT_DIR}/qwen_transcribe.py" \
  "$TMPDIR_PATH/audio.mp3" \
  "$TMPDIR_PATH/${FILENAME}"
T1=$(date +%s)
TRANSCRIBE_SECS=$((T1 - T0))

# Build the metadata sidecar
META_JSON=$(python3 -c 'import json,sys; print(json.dumps({
  "url": sys.argv[1],
  "title": sys.argv[2],
  "duration_seconds": round(float(sys.argv[3]), 1),
  "transcribe_seconds": int(sys.argv[4]),
}, indent=2))' "$YOUTUBE_URL" "$RAW_TITLE" "$DURATION" "$TRANSCRIBE_SECS")

REMOTE_TXT="transcripts/${FILENAME}"
REMOTE_META="transcripts/${BASE}.meta.json"

if [ -n "${SCRIBE_NO_UPLOAD:-}" ]; then
  # Local mode: write into the repo's transcripts/ (committed by the caller)
  mkdir -p "${SCRIPT_DIR}/transcripts"
  cp "$TMPDIR_PATH/${FILENAME}" "${SCRIPT_DIR}/${REMOTE_TXT}"
  printf '%s\n' "$META_JSON" > "${SCRIPT_DIR}/${REMOTE_META}"
  echo "💾 Wrote ${REMOTE_TXT} and ${REMOTE_META} locally (upload skipped)."
else
  # Upload transcript + meta to GitHub via the Contents API (idempotent upsert)
  gh_put() {  # remote_path  local_file  message
    local remote="$1" localf="$2" msg="$3" content sha
    content=$(base64 < "$localf" | tr -d '\n')
    sha=$(gh api "repos/${REPO}/contents/${remote}" --jq '.sha' 2>/dev/null || echo "")
    if [ -n "$sha" ]; then
      gh api --method PUT "repos/${REPO}/contents/${remote}" \
        -f message="$msg" -f content="$content" -f sha="$sha" --silent
    else
      gh api --method PUT "repos/${REPO}/contents/${remote}" \
        -f message="$msg" -f content="$content" --silent
    fi
  }

  printf '%s\n' "$META_JSON" > "$TMPDIR_PATH/${BASE}.meta.json"
  echo "☁️  Uploading transcript + metadata to github.com/${REPO}/transcripts/..."
  gh_put "$REMOTE_TXT"  "$TMPDIR_PATH/${FILENAME}"        "transcript: ${FILENAME}"
  gh_put "$REMOTE_META" "$TMPDIR_PATH/${BASE}.meta.json"  "transcript meta: ${BASE}"
  echo "✅ Done → github.com/${REPO}/blob/main/${REMOTE_TXT}"
fi
