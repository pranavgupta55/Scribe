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

# 3. Make scribe.sh executable
chmod +x "${REPO_DIR}/scribe.sh"

# 4. Write SCRIBE_HOME and PATH entry to shell rc (idempotent)
if ! grep -q "SCRIBE_HOME" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# Scribe — YouTube transcription tool"
    echo "export SCRIBE_HOME=\"${REPO_DIR}\""
    echo "export PATH=\"\$PATH:\$SCRIBE_HOME\""
  } >> "$SHELL_RC"
  echo "✅ Added SCRIBE_HOME and PATH to ${SHELL_RC}"
else
  echo "ℹ️  SCRIBE_HOME already set in ${SHELL_RC} — skipping"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "Reload your shell, then run:"
echo "  scribe.sh <youtube_url> [output_dir_relative_to_home]"
echo ""
echo "Example:"
echo "  scribe.sh https://youtube.com/watch?v=... Videos/transcripts"
echo "  scribe.sh https://youtube.com/watch?v=...          # saves to ~/Desktop"
