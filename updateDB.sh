#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# updateDB.sh — Pull new transcripts from GitHub and process them into the
#               knowledge base (ChromaDB embeddings + Markdown topic notes).
#
# Run this after scribe.sh has uploaded new transcripts.
#
# Usage:
#   updateDB.sh            # process any new unprocessed transcripts
#   updateDB.sh --rebuild  # wipe ChromaDB and reprocess everything from scratch
# ---------------------------------------------------------------------------

SCRIPT_DIR="${SCRIBE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO="${SCRIBE_REPO:-pranavgupta55/Scribe}"
MODE="${1:-}"

# 1. Pull latest (gets new transcripts uploaded by scribe.sh)
echo "📥 Pulling latest from GitHub..."
cd "$SCRIPT_DIR"
git pull --quiet --rebase

# 2. Verify Ollama is running
if ! ollama list &>/dev/null 2>&1; then
  echo "❌ Ollama is not running."
  echo "   Start it with: ollama serve"
  echo "   Or open the Ollama app from your Applications folder."
  exit 1
fi

# 3. Process transcripts
if [ "$MODE" = "--rebuild" ]; then
  echo "🔄 Rebuilding knowledge base from scratch..."
  python3 "${SCRIPT_DIR}/process.py" --rebuild
else
  echo "🧠 Processing new transcripts..."
  python3 "${SCRIPT_DIR}/process.py" --all
fi

# 4. Commit and push knowledge updates
echo ""
echo "☁️  Pushing knowledge updates..."
git add knowledge/
if git diff --staged --quiet; then
  echo "ℹ️  Knowledge base is already up to date."
else
  git commit -m "knowledge: update from processed transcripts"
  git push
  echo "✅ Knowledge base pushed → github.com/${REPO}/tree/main/knowledge"
fi
