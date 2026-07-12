# M4 batch ingest — MoreMozi remainder (900 videos)

> **Purpose:** M4 completes its assigned share of the MoreMozi channel. M5 has already transcribed 380 of its own 580-video slice and pushed to `origin/main`; ~200 M5 slice remain (M5 pipeline currently paused). M4's assignment is the 900-video `moremozi_batch.txt` set, disjoint from M5's — no coordination needed except atomic per-file pushes.
>
> **You (the M4 Claude agent) are on the Anthropic Claude Code subscription (NOT the direct API). Do NOT deliberate. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-moremozi/` (any existing install is assumed stale).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time; skipped if models present).
- Transcribes 900 MoreMozi videos serially on M4 (~15-25 h wall-clock over multiple sessions — ~90 h of audio at ~10× realtime on M4 GPU). Resumable + quit-safe.
- Uploads each transcript + `.meta.json` sidecar directly to `github.com/pranavgupta55/Scribe` via GitHub API (atomic per-file push to `origin/main`, no PR).
- **Does NOT run knowledge extraction** — M5 handles Phase 3a → Phase 6 downstream.

**Expected wall-clock: overnight × 2-3 nights.** Runs unattended between sessions.

---

## 1. Prerequisites

```bash
command -v gh      >/dev/null || { echo "MISSING: gh CLI (brew install gh)"; exit 1; }
command -v ffmpeg  >/dev/null || { echo "MISSING: ffmpeg (brew install ffmpeg)"; exit 1; }
command -v python3 >/dev/null || { echo "MISSING: python3"; exit 1; }
gh auth status     >/dev/null 2>&1 || { echo "MISSING: gh auth login (run interactively)"; exit 1; }
echo "prereqs OK"
```

If `gh auth login` is needed, the human runs it — don't try to bypass.

---

## 2. Fresh clone + setup

```bash
mkdir -p ~/Desktop
cd ~/Desktop
rm -rf scribe-m4-moremozi
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-moremozi
cd scribe-m4-moremozi
bash setup.sh
```

`setup.sh` pulls ollama models (~2 min, or skipped if present). If it errors, print the last 30 lines and stop.

---

## 3. Sync assignment from remote

The 900-video queue was computed on M5 and pushed as `.forge_scratch/m4_remainder.txt` — but that path is gitignored. It also lives inline below (source of truth).

To reconstruct M4's queue locally from the tracked catalog + on-disk state:

```bash
python3 << 'EOF'
import os, re
# M4's assignment = .queue/moremozi_batch.txt entries whose YouTube ID is NOT already a transcript on this clone (fresh clone → 0 already done initially, all 900 fresh)
target = set()
with open('.queue/moremozi_batch.txt') as f:
    for line in f:
        m = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', line)
        if m: target.add((m.group(1), line.strip()))

on_disk = set()
for f in os.listdir('transcripts'):
    if not f.endswith('.txt'): continue
    m = re.search(r'_([A-Za-z0-9_-]{11})\.txt$', f)
    if m: on_disk.add(m.group(1))

remaining = [(vid, line) for vid, line in target if vid not in on_disk]
print(f'M4 remaining: {len(remaining)} (target {len(target)}, already-done {len(target)-len(remaining)})')

# Write M4 queue file — resume-safe
with open('.queue/m4_moremozi_queue.txt','w') as out:
    for vid, line in remaining:
        out.write(line + '\n')
print('wrote .queue/m4_moremozi_queue.txt')
EOF
```

**Expected output on first run:** `M4 remaining: 900`. On resume, expect it to shrink as work completes.

---

## 4. Ingest driver (based on ingest_m5.sh)

Save as `ingest_m4_moremozi.sh` in `~/Desktop/scribe-m4-moremozi/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Each transcript pushes to GitHub as its own commit. M5 may push its own
# unrelated files concurrently — atomic per-file upload avoids conflict.
unset SCRIBE_NO_UPLOAD

QUEUE=".queue/m4_moremozi_queue.txt"
DONE=".queue/done.txt"
FAILED=".queue/failed.txt"
LOG="ingest_m4_moremozi.log"

touch "$DONE" "$FAILED" "$LOG"

TOTAL=$(wc -l < "$QUEUE" | tr -d ' ')
i=0
while IFS='|' read -r URL_FIELD CHANNEL DUR TITLE; do
  i=$((i+1))

  # Extract clean URL from "N\thttps://youtu.be/ID" format
  URL=$(echo "$URL_FIELD" | grep -oE 'https://youtu\.be/[A-Za-z0-9_-]{11}')
  VID=$(echo "$URL" | grep -oE '[A-Za-z0-9_-]{11}$')

  # Skip if already done
  if grep -qF "$VID" "$DONE" 2>/dev/null; then
    echo "[$i/$TOTAL] SKIP: $VID" | tee -a "$LOG"
    continue
  fi
  # Skip if permanently failed
  if grep -qF "$VID" "$FAILED" 2>/dev/null; then
    echo "[$i/$TOTAL] FAIL_SKIP: $VID" | tee -a "$LOG"
    continue
  fi
  # Cooperative stop
  if [ -f .queue/STOP ]; then
    echo "STOP flag detected — exiting cleanly" | tee -a "$LOG"
    break
  fi

  # Slug from title (lowercase, safe chars)
  SLUG=$(echo "moremozi_$TITLE" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_' '_' | sed 's/__*/_/g' | cut -c1-80 | sed 's/_$//')
  SLUG="${SLUG}_${VID}"

  echo "[$i/$TOTAL] START: $SLUG (${DUR}s)" | tee -a "$LOG"
  START=$(date +%s)

  if bash scribe.sh "$URL" "$SLUG" >>"$LOG" 2>&1; then
    ELAPSED=$(( $(date +%s) - START ))
    echo "[$i/$TOTAL] OK (${ELAPSED}s): $SLUG" | tee -a "$LOG"
    echo "$VID" >> "$DONE"
  else
    echo "[$i/$TOTAL] FAIL: $SLUG" | tee -a "$LOG"
    echo "$VID yt-dlp-fail $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$FAILED"
  fi
done < "$QUEUE"

echo "======================================" | tee -a "$LOG"
DONE_COUNT=$(wc -l < "$DONE" | tr -d ' ')
FAIL_COUNT=$(wc -l < "$FAILED" | tr -d ' ')
echo "M4 MoreMozi done: done_total=$DONE_COUNT fail_total=$FAIL_COUNT" | tee -a "$LOG"
```

Make executable + run:

```bash
chmod +x ingest_m4_moremozi.sh
nohup ./ingest_m4_moremozi.sh >>ingest_m4_moremozi.log 2>&1 &
echo "M4 MoreMozi ingest started (PID $!)"
```

Quit early: `touch .queue/STOP` (checked before each video). Resume: re-run — skips whatever's already in `.queue/done.txt`.

---

## 5. What NOT to do

- **Do NOT touch `knowledge/`, `graph/`, `.scribe-skills/`, or `.chroma/`** — these belong to the M5-side extraction pipeline. Any commit changing those files creates a merge conflict for M5.
- **Do NOT run `updateDB.sh`, `process.py`, `claudeProcess.py`, or `serve.sh`.**
- **Do NOT bulk-commit** — one commit per transcript pair (`.txt` + `.meta.json`). This is what `scribe.sh` already does via GitHub API; don't override.
- **Do NOT modify `.gitignore` or `docs/`** — M5 owns those.
- **If yt-dlp hits "Sign in to confirm you're not a bot":** wait 6-12 h or add `--cookies-from-browser chrome` to the yt-dlp calls in `scribe.sh`. Not a bug.

---

## 6. Reporting back to M5

When done (or paused overnight), the M5 side sees your progress via `git pull origin main`. No coordination file needed. If something unusual happens (repeated bot-blocks, disk full, model corruption), append a note to this file (`docs/M4_MOREMOZI_REMAINDER.md`) under a new `## Status` section, commit, and push.

---

## 7. Safe pull-back for M5

On M5 side, `git pull origin main` after your session is always safe: you only touch `transcripts/` + `.queue/done.txt` + `.queue/failed.txt` + `ingest_m4_moremozi.log`. None of those conflict with M5's `knowledge/` / `graph/` work.

---

## Appendix: assignment source of truth

`.queue/moremozi_batch.txt` in the repo root (900 lines) is the canonical M4 assignment. Format: `N\thttps://youtu.be/<VID>|MoreMozi|<duration_s>|<title>`. Never edit this file — filter via `.queue/done.txt` / `.queue/failed.txt` at ingest time (as the script does above).
