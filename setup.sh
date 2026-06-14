#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_RC="${HOME}/.zshrc"

echo "=== Scribe setup ==="

# 1. Homebrew
if ! command -v brew &>/dev/null; then
  echo "❌ Homebrew not found. Install it from https://brew.sh then re-run this script."
  exit 1
fi

# 2. System packages
echo "📦 Installing system dependencies (ffmpeg, ollama)..."
brew install ffmpeg --quiet
brew install ollama --quiet

# 3. Python packages
echo "🐍 Installing Python packages..."
pip3 install --quiet torch torchaudio transformers accelerate tqdm yt-dlp chromadb ollama

# 4. GitHub CLI
if ! command -v gh &>/dev/null; then
  echo "📦 Installing GitHub CLI..."
  brew install gh --quiet
fi
if ! gh auth status &>/dev/null 2>&1; then
  echo ""
  echo "⚠️  GitHub CLI is not authenticated. Run the following, then re-run setup.sh:"
  echo "    gh auth login"
  exit 1
fi

# 5. Pull Ollama models
echo "🤖 Pulling Ollama models (this downloads ~1.4 GB — takes 2-4 min on fast internet)..."
ollama pull qwen3:1.7b
ollama pull nomic-embed-text

# 6. Make scripts executable
chmod +x "${REPO_DIR}/scribe.sh"
chmod +x "${REPO_DIR}/updateDB.sh"

# 7. Write env vars to shell rc (idempotent)
if ! grep -q "SCRIBE_HOME" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# Scribe — YouTube transcription + knowledge base"
    echo "export SCRIBE_HOME=\"${REPO_DIR}\""
    echo "export SCRIBE_REPO=\"pranavgupta55/Scribe\""
    echo "export PATH=\"\$PATH:\$SCRIBE_HOME\""
  } >> "$SHELL_RC"
  echo "✅ Added SCRIBE_HOME, SCRIBE_REPO, and PATH to ${SHELL_RC}"
else
  echo "ℹ️  SCRIBE_HOME already in ${SHELL_RC} — skipping"
fi

echo ""
echo "✅ Setup complete! Reload your shell:"
echo "    source ~/.zshrc"
echo ""
echo "Workflow:"
echo "    scribe.sh <youtube_url> [filename]   # transcribe + push to GitHub"
echo "    updateDB.sh                          # pull + process into knowledge base"
