# M4 MoreMozi ingest — RUN NOW

**You are M4. This machine's only job right now = transcribe 900 MoreMozi YouTube videos via local Whisper and push each transcript to GitHub. NO LLM calls. NO knowledge extraction. NO analysis. NO discussion. Copy-paste the blocks below in order. If a step errors, print the last 30 lines and stop.**

M5 handles all LLM work (Phase 3a→6). You do CPU-only Whisper. Do not touch `knowledge/`, `graph/`, `.scribe-skills/`, `.chroma/`, `process.py`, `claudeProcess.py`, `updateDB.sh`, `server.py`.

Your work is atomic per-file GitHub API push. No PRs. No branches. Safe to run alongside M5's pushes.

---

## STEP 1 — Prereqs (run once, print "prereqs OK")

```bash
command -v gh      >/dev/null || { echo "MISSING: gh (brew install gh)";     exit 1; }
command -v ffmpeg  >/dev/null || { echo "MISSING: ffmpeg (brew install ffmpeg)"; exit 1; }
command -v python3 >/dev/null || { echo "MISSING: python3"; exit 1; }
command -v yt-dlp  >/dev/null || pip3 install --user yt-dlp
gh auth status     >/dev/null 2>&1 || { echo "RUN INTERACTIVELY: gh auth login"; exit 1; }
echo "prereqs OK"
```

If `gh auth login` needed → tell the human, STOP. Do not attempt token workarounds.

---

## STEP 2 — Fresh clone + setup (run once, ~2-5 min)

```bash
mkdir -p ~/Desktop
cd ~/Desktop
rm -rf scribe-m4-moremozi
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-moremozi
cd scribe-m4-moremozi
bash setup.sh
```

If `setup.sh` errors → print last 30 lines, STOP. Do not skip.

---

## STEP 3 — Build M4 queue from remote catalog (10 seconds)

```bash
cd ~/Desktop/scribe-m4-moremozi
python3 << 'EOF'
import os, re
target = []
with open('.queue/moremozi_batch.txt') as f:
    for line in f:
        m = re.search(r'youtu\.be/([A-Za-z0-9_-]{11})', line)
        if m: target.append((m.group(1), line.strip()))

on_disk = set()
for f in os.listdir('transcripts'):
    if not f.endswith('.txt'): continue
    m = re.search(r'_([A-Za-z0-9_-]{11})\.txt$', f)
    if m: on_disk.add(m.group(1))

remaining = [(vid, line) for vid, line in target if vid not in on_disk]
with open('.queue/m4_moremozi_queue.txt','w') as out:
    for vid, line in remaining:
        out.write(line + '\n')
print(f'M4 remaining: {len(remaining)} of {len(target)}')
EOF
```

Expected: `M4 remaining: 900` on first run. Later runs: shrinks as work completes.

---

## STEP 4 — Save the ingest script (copy-paste as-is)

Save exactly as `ingest_m4_moremozi.sh` in `~/Desktop/scribe-m4-moremozi/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"
unset SCRIBE_NO_UPLOAD

QUEUE=".queue/m4_moremozi_queue.txt"
DONE=".queue/done.txt"
FAILED=".queue/failed.txt"
LOG="ingest_m4_moremozi.log"
touch "$DONE" "$FAILED" "$LOG"

TOTAL=$(wc -l < "$QUEUE" | tr -d ' ')
i=0
while IFS= read -r LINE; do
  i=$((i+1))
  URL=$(echo "$LINE" | grep -oE 'https://youtu\.be/[A-Za-z0-9_-]{11}')
  VID=$(echo "$URL" | grep -oE '[A-Za-z0-9_-]{11}$')
  TITLE=$(echo "$LINE" | awk -F'|' '{print $NF}')
  DUR=$(echo "$LINE" | awk -F'|' '{print $(NF-1)}')

  if grep -qF "$VID" "$DONE"   2>/dev/null; then echo "[$i/$TOTAL] SKIP $VID"      | tee -a "$LOG"; continue; fi
  if grep -qF "$VID" "$FAILED" 2>/dev/null; then echo "[$i/$TOTAL] FAIL_SKIP $VID" | tee -a "$LOG"; continue; fi
  if [ -f .queue/STOP ]; then echo "STOP flag set — exit" | tee -a "$LOG"; break; fi

  SLUG=$(echo "moremozi_$TITLE" | tr '[:upper:]' '[:lower:]' \
    | tr -c 'a-z0-9_' '_' | sed 's/__*/_/g' | cut -c1-80 | sed 's/_$//')
  SLUG="${SLUG}_${VID}"

  echo "[$i/$TOTAL] START $SLUG (${DUR}s)" | tee -a "$LOG"
  START=$(date +%s)
  if bash scribe.sh "$URL" "$SLUG" >>"$LOG" 2>&1; then
    ELAPSED=$(( $(date +%s) - START ))
    echo "[$i/$TOTAL] OK ${ELAPSED}s $SLUG" | tee -a "$LOG"
    echo "$VID" >> "$DONE"
  else
    echo "[$i/$TOTAL] FAIL $SLUG" | tee -a "$LOG"
    echo "$VID yt-dlp-fail $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$FAILED"
  fi
done < "$QUEUE"

DONE_COUNT=$(wc -l < "$DONE" | tr -d ' ')
FAIL_COUNT=$(wc -l < "$FAILED" | tr -d ' ')
echo "M4 MoreMozi run end: done_total=$DONE_COUNT fail_total=$FAIL_COUNT" | tee -a "$LOG"
```

Make executable:

```bash
chmod +x ingest_m4_moremozi.sh
```

---

## STEP 5 — LAUNCH IN BACKGROUND (single command)

```bash
cd ~/Desktop/scribe-m4-moremozi
nohup ./ingest_m4_moremozi.sh >>ingest_m4_moremozi.log 2>&1 &
echo "M4 ingest started PID $!"
disown
```

After this: **your job for the next many hours is nothing.** Whisper does the work. Terminal free.

---

## STEP 6 — Verify running (single command, 5 seconds)

```bash
sleep 30 && tail -5 ~/Desktop/scribe-m4-moremozi/ingest_m4_moremozi.log
```

Expect a `[1/900] START ...` or `[1/900] OK ...` line. If the log is empty after 30s → `ps aux | grep ingest_m4`. If nothing running, re-run STEP 5.

---

## STEP 7 — Idle. Report status hourly.

Every hour print one line and STOP:

```bash
tail -1 ~/Desktop/scribe-m4-moremozi/ingest_m4_moremozi.log
wc -l ~/Desktop/scribe-m4-moremozi/.queue/done.txt ~/Desktop/scribe-m4-moremozi/.queue/failed.txt
```

Do not spawn agents. Do not run any LLM calls. Do not open files in `knowledge/`, `graph/`, `.scribe-skills/`, `.chroma/`.

---

## STOP CONDITIONS

- Cooperative stop mid-run: `touch ~/Desktop/scribe-m4-moremozi/.queue/STOP` (finishes current video, then exits cleanly). Resume: rerun STEP 5.
- Hard kill: `pkill -f ingest_m4_moremozi.sh`. Resume: rerun STEP 5.
- Full ~900-video pass expected ~15-25 h split across sessions.

---

## FAILURE MODES (do exactly this — do not improvise)

| Symptom | Action |
|---|---|
| `Sign in to confirm you're not a bot` (in log) | Wait 6-12 h then rerun STEP 5. Alt: prepend `--cookies-from-browser chrome` to yt-dlp calls inside `scribe.sh`. |
| Disk full | `df -h ~/Desktop` — if <5GB free, tell the human, STOP. |
| `setup.sh` reruns download every time | Set `HF_HOME` + `OLLAMA_MODELS` env in `~/.zshrc` per your prior session. Don't re-download. |
| gh auth expired mid-run | Restart script after `gh auth login`. In-flight OK — `done.txt` protects progress. |
| Same VID keeps failing 3+ times | Append VID to `.queue/failed.txt` manually, keep going. |

---

## RULES

- **DO NOT** touch `knowledge/`, `graph/`, `.scribe-skills/`, `.chroma/`.
- **DO NOT** run `updateDB.sh`, `process.py`, `claudeProcess.py`, `serve.sh`, `server.py`.
- **DO NOT** modify `.gitignore`, `docs/`, this file.
- **DO NOT** bulk-commit. `scribe.sh` does atomic per-file gh-API upload — don't override.
- **DO NOT** spawn sub-agents. **DO NOT** use `Workflow`. **DO NOT** invoke any LLM.
- **DO NOT** think about extraction, embeddings, RAG, or graph — none of that is your job.

The queue file `.queue/m4_moremozi_queue.txt` is your only source of truth. Follow it. Push each result. Idle. That's it.

---

## APPENDIX — reference paths

- Queue source of truth (tracked in repo): `.queue/moremozi_batch.txt` (900 lines, do not edit)
- Live queue (regenerated each session via STEP 3): `.queue/m4_moremozi_queue.txt`
- Progress: `.queue/done.txt`, `.queue/failed.txt`
- Log: `ingest_m4_moremozi.log`
- Ingest driver: `ingest_m4_moremozi.sh`
- This doc (canonical): `docs/M4_MOREMOZI_REMAINDER.md`

M5 pulls your pushes via `git pull origin main`. No coordination file needed.
