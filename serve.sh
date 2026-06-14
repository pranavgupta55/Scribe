#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# serve.sh — Regenerate graph/graph.json from the knowledge base and serve the
#             Scribe knowledge graph + RAG chat in your browser.
#
# Usage:
#   serve.sh
# ---------------------------------------------------------------------------

SCRIPT_DIR="${SCRIBE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PORT=8765

cd "$SCRIPT_DIR"

echo "📊 Generating graph data..."
python3 "${SCRIPT_DIR}/export_graph.py"

echo ""
echo "🌐 Starting server at http://localhost:${PORT}/graph/index.html"
echo "   Graph + RAG chat · Press Ctrl+C to stop"
echo ""

open "http://localhost:${PORT}/graph/index.html"
python3 "${SCRIPT_DIR}/server.py"
