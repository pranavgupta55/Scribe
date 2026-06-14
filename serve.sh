#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# serve.sh — Regenerate graph/graph.json from the knowledge base and open
#             the Scribe knowledge graph in your browser.
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
echo "   Press Ctrl+C to stop"
echo ""

open "http://localhost:${PORT}/graph/index.html"
python3 -m http.server ${PORT} --directory "${SCRIPT_DIR}"
