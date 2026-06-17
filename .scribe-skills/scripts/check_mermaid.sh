#!/usr/bin/env bash
# Validate Mermaid diagrams in markdown files BEFORE pushing to GitHub.
# Usage: ./check_mermaid.sh ARCHITECTURE.md  (or any .md with ```mermaid blocks)
# Exits 0 if all diagrams parse, non-zero otherwise.
#
# Why this exists: GitHub's Mermaid renderer is stricter than the live editor.
# Common breakers found while building this repo:
#   - parens or other special chars inside stadium nodes (use [\"...\"] quoting)
#   - '<' or '>' inside |...| edge labels (Mermaid treats them as HTML)
#   - dotted-arrow labels need spaces:  A -. label .-> B  (not -.label.->)
#
# Prerequisites (installed lazily on first run):
#   - node on PATH
#   - npm packages: mermaid jsdom dompurify  (installed into the WORKDIR below)

WORKDIR="${SCRIBE_MM_WORKDIR:-/tmp/scribe_mmcheck}"
FILE="${1:?usage: $0 <markdown_file>}"

if [ ! -f "$FILE" ]; then
  echo "no such file: $FILE" >&2
  exit 2
fi

# Lazy install
if [ ! -d "$WORKDIR/node_modules/mermaid" ]; then
  echo "[check_mermaid] first run — installing harness in $WORKDIR ..."
  mkdir -p "$WORKDIR"
  ( cd "$WORKDIR" && npm init -y >/dev/null && npm install --silent mermaid jsdom dompurify ) || {
    echo "[check_mermaid] npm install failed" >&2; exit 2;
  }
fi

# Extract every ```mermaid block from the file
python3 - "$FILE" "$WORKDIR/blocks.json" <<'PY'
import json, re, sys
text = open(sys.argv[1]).read()
blocks = re.findall(r"```mermaid\n(.*?)\n```", text, re.S)
open(sys.argv[2], "w").write(json.dumps(blocks))
print(f"[check_mermaid] {len(blocks)} mermaid block(s) found in {sys.argv[1]}")
PY

# Run mermaid.parse() against each block (no browser — just the grammar)
cat > "$WORKDIR/check.mjs" <<JS
import { JSDOM } from 'jsdom'
import fs from 'fs'
const dom = new JSDOM('<!doctype html><html><body></body></html>')
globalThis.window = dom.window
globalThis.document = dom.window.document
const dompurify = (await import('dompurify')).default
globalThis.DOMPurify = dompurify(dom.window)

const mermaid = (await import('mermaid')).default
mermaid.initialize({ startOnLoad: false })

const blocks = JSON.parse(fs.readFileSync('$WORKDIR/blocks.json', 'utf8'))
let ok = 0, fail = 0
for (let i = 0; i < blocks.length; i++) {
  try {
    await mermaid.parse(blocks[i])
    console.log(\`[\${i+1}/\${blocks.length}] OK\`)
    ok++
  } catch (e) {
    console.log(\`[\${i+1}/\${blocks.length}] FAIL\`)
    const msg = e.message || e.str || JSON.stringify(e).slice(0, 800)
    console.log(msg.split('\n').slice(0, 10).join('\n'))
    fail++
  }
}
console.log(\`\n=== \${ok} ok / \${fail} fail (of \${blocks.length}) ===\`)
process.exit(fail === 0 ? 0 : 1)
JS

cd "$WORKDIR" && node check.mjs
