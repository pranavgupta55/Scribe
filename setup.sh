#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_RC="${HOME}/.zshrc"
MODELS_DIR="${REPO_DIR}/models"

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
pip3 install --quiet torch torchaudio transformers accelerate tqdm \
  yt-dlp chromadb ollama numpy google-genai

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

# 5. Create models directory
mkdir -p "${MODELS_DIR}/hf"
mkdir -p "${MODELS_DIR}/ollama"

# 6. Pull Ollama models into the Scribe models directory
echo "🤖 Pulling Ollama models into ${MODELS_DIR}/ollama (~6.4 GB total)..."
OLLAMA_MODELS="${MODELS_DIR}/ollama" ollama pull qwen3:1.7b
OLLAMA_MODELS="${MODELS_DIR}/ollama" ollama pull nomic-embed-text
# qwen3-embedding:8b is used by the v2 graph-rebuild pipeline (Phase 1a/3b/4)
# for high-quality 4096-d clustering + connection candidates. Skip if you
# only run the v1 lightweight pipeline (updateDB.sh + process.py).
OLLAMA_MODELS="${MODELS_DIR}/ollama" ollama pull qwen3-embedding:8b

# 7. Make all scripts executable
chmod +x "${REPO_DIR}/scribe.sh"
chmod +x "${REPO_DIR}/updateDB.sh"
chmod +x "${REPO_DIR}/serve.sh"

# 8. Write env vars to shell rc (idempotent)
if ! grep -q "SCRIBE_HOME" "$SHELL_RC" 2>/dev/null; then
  {
    echo ""
    echo "# Scribe — YouTube transcription + knowledge base"
    echo "export SCRIBE_HOME=\"${REPO_DIR}\""
    echo "export SCRIBE_REPO=\"pranavgupta55/Scribe\""
    echo "export HF_HOME=\"${MODELS_DIR}/hf\""
    echo "export OLLAMA_MODELS=\"${MODELS_DIR}/ollama\""
    echo "export PATH=\"\$PATH:\$SCRIBE_HOME\""
  } >> "$SHELL_RC"
  echo "✅ Added SCRIBE_HOME, SCRIBE_REPO, HF_HOME, OLLAMA_MODELS, PATH to ${SHELL_RC}"
else
  echo "ℹ️  SCRIBE_HOME already in ${SHELL_RC} — skipping shell config"
fi

echo ""
echo "✅ Setup complete! Reload your shell:"
echo "    source ~/.zshrc"
echo ""
echo "🔑 The graph's RAG chat uses Google Gemini (free tier). Add a key:"
echo "    1. Create one at https://aistudio.google.com/apikey"
echo "    2. echo 'export GEMINI_API_KEY=\"AIza...\"' >> ~/.zshrc && source ~/.zshrc"
echo "   (Transcription + knowledge extraction run locally and need no key.)"
echo ""
echo "Commands:"
echo "    scribe.sh <youtube_url> [filename]   # transcribe + push to GitHub"
echo "    updateDB.sh                          # pull + process into knowledge base"
echo "    serve.sh                             # open knowledge graph + RAG chat in browser"
