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

echo "📊 Generating graph data (v1 + v2)..."
python3 "${SCRIPT_DIR}/export_graph.py"
if [ -f "${SCRIPT_DIR}/export_graph_v2.py" ] && [ -f "${SCRIPT_DIR}/knowledge/v2/nodes.jsonl" ]; then
  python3 "${SCRIPT_DIR}/export_graph_v2.py"
fi

echo ""
echo "🌐 Starting server at http://localhost:${PORT}/graph/index.html"
echo "   Graph + RAG chat · Press Ctrl+C to stop"
echo ""

open "http://localhost:${PORT}/graph/index.html"
# ADR-0007: exec hands the script over to python so we don't carry a
# bash parent waiting on the child. Nothing after this line will run.
exec python3 "${SCRIPT_DIR}/server.py"
