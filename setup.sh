#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_RC="${HOME}/.zshrc"

echo "=== Scribe setup ==="

# 1. System dependencies
if ! command -v brew &>/dev/null; then
  echo "❌ Homebrew not found. Install it from https://brew.sh then re-run this script."
  exit 1
fi

echo "📦 Installing ffmpeg (via Homebrew)..."
brew install ffmpeg --quiet

# 2. Python packages
echo "🐍 Installing Python packages..."
pip3 install --quiet torch torchaudio transformers accelerate tqdm yt-dlp

# 3. GitHub CLI
if ! command -v gh &>/dev/null; then
  echo "📦 Installing GitHub CLI..."
  brew install gh --quiet
fi
if ! gh auth status &>/dev/null; then
  echo ""
  echo "⚠️  GitHub CLI is not authenticated. Run the following, then re-run setup.sh:"
  echo "    gh auth login"
  exit 1
fi

# 4. Make scripts executable
chmod +x "${REPO_DIR}/scribe.sh"

# 5. Write env vars to shell rc (idempotent)
if ! grep -q "SCRIBE_HOME" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# Scribe — YouTube transcription tool"
    echo "export SCRIBE_HOME=\"${REPO_DIR}\""
    echo "export SCRIBE_REPO=\"pranavgupta55/Scribe\""
    echo "export PATH=\"\$PATH:\$SCRIBE_HOME\""
  } >> "$SHELL_RC"
  echo "✅ Added SCRIBE_HOME, SCRIBE_REPO, and PATH to ${SHELL_RC}"
else
  echo "ℹ️  SCRIBE_HOME already set in ${SHELL_RC} — skipping"
fi

echo ""
echo "✅ Setup complete! Reload your shell:"
echo "    source ~/.zshrc"
echo ""
echo "Usage:"
echo "    scribe.sh <youtube_url>                  # filename derived from video title"
echo "    scribe.sh <youtube_url> my-filename      # explicit filename"
