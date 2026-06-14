#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# scribe.sh — Download a YouTube video and transcribe it with Qwen3-ASR
#
# Usage:
#   scribe.sh <youtube_url> [output_dir]
#
# output_dir is relative to ~/ and defaults to Desktop
# ---------------------------------------------------------------------------

YOUTUBE_URL="${1:?Error: YouTube URL required.  Usage: scribe.sh <url> [output_dir]}"
OUTPUT_DIR_REL="${2:-Desktop}"
OUTPUT_DIR="${HOME}/${OUTPUT_DIR_REL}"
SCRIPT_DIR="${SCRIBE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

mkdir -p "$OUTPUT_DIR"

# Fetch video title and sanitise it into a safe filename
echo "🔍 Fetching video info..."
RAW_TITLE=$(yt-dlp --print title "$YOUTUBE_URL" 2>/dev/null)
SAFE_TITLE=$(echo "$RAW_TITLE" | tr '[:upper:]' '[:lower:]' \
  | sed 's/[^a-z0-9]/_/g' \
  | sed 's/__*/_/g' \
  | sed 's/^_//;s/_$//')

OUTPUT_FILE="${OUTPUT_DIR}/${SAFE_TITLE}.txt"

# Download audio into a temp directory that is always cleaned up on exit
TMPDIR_PATH=$(mktemp -d)
trap 'rm -rf "$TMPDIR_PATH"' EXIT

echo "⬇️  Downloading audio: \"${RAW_TITLE}\""
yt-dlp \
  --extract-audio \
  --audio-format mp3 \
  --audio-quality 0 \
  --output "$TMPDIR_PATH/audio.%(ext)s" \
  --quiet --progress \
  "$YOUTUBE_URL"

AUDIO_FILE="$TMPDIR_PATH/audio.mp3"

echo "🤖 Starting transcription..."
python3 "${SCRIPT_DIR}/qwen_transcribe.py" "$AUDIO_FILE" "$OUTPUT_FILE"
