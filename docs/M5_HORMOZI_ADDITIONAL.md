# M5 batch ingest: Additional @alexhormozi videos (recent uploads) → Scribe

> **Purpose:** M5 transcribes newly-posted Alex Hormozi videos (published between the first channel scan and today, 2026-07-04). These are additions to the existing ~295-video Hormozi corpus already in Scribe. Single-machine run.
>
> **You (the M5 Claude agent) are on direct API billing. Do not deliberate. Do not restructure. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m5-hormozi/` (any existing Scribe install is assumed broken).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time; skipped if models already present).
- Transcribes newly-posted Hormozi videos (~12-14 h wall-clock on M5 GPU; varies by video count & audio length).
- Uploads each transcript + `.meta.json` sidecar directly to `github.com/pranavgupta55/Scribe` via GitHub API (atomic per-file push to `origin/main`, no PR).
- **Does NOT run `updateDB.sh`** — knowledge extraction is a separate step run manually after M5 finishes.

**Expected wall-clock: ~12-14 h unattended. Runs comfortably overnight.**

---

## SCOPE NOTE

Task: Ingest all Hormozi videos posted to @alexhormozi since the initial channel scan (approx. mid-June 2026).
- New long-form videos (YouTube main channel)
- New Shorts (YouTube Shorts)
- Podcast appearances on Hormozi's own channel

Exclusions: External podcast appearances, unlisted/private videos, duplicates.

---

## 1. Prerequisites

```bash
command -v gh      >/dev/null || { echo "MISSING: gh CLI"; exit 1; }
command -v ffmpeg  >/dev/null || { echo "MISSING: ffmpeg"; exit 1; }
command -v python3 >/dev/null || { echo "MISSING: python3"; exit 1; }
command -v yt-dlp  >/dev/null || { echo "MISSING: yt-dlp"; exit 1; }
gh auth status     >/dev/null 2>&1 || { echo "MISSING: gh auth"; exit 1; }
echo "prereqs OK"
```

---

## 2. Fresh clone + setup

```bash
mkdir -p ~/Desktop
cd ~/Desktop
rm -rf scribe-m5-hormozi
git clone https://github.com/pranavgupta55/Scribe.git scribe-m5-hormozi
cd scribe-m5-hormozi
bash setup.sh
```

---

## 3. Fetch @alexhormozi videos posted after 2026-06-15

```bash
CHANNEL_URL="https://www.youtube.com/@alexhormozi"

# Get videos newer than June 15
yt-dlp --flat-playlist "$CHANNEL_URL" --print id,title,upload_date,duration   --dateafter 20260615 2>/dev/null > new_videos_raw.txt

# Validate
head -20 new_videos_raw.txt
```

If empty, stop and print: `NO NEW HORMOZI VIDEOS SINCE 2026-06-15`

---

## 4. Build video list

From `new_videos_raw.txt`, construct VIDEOS array in ingest_m5.sh below.

Long-form slug: lowercase, underscore-separated, max 50 chars
Shorts slug: `hormozi_short_<video_id>`

---

## 5. Ingest script (save as ingest_m5.sh)

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

unset SCRIBE_NO_UPLOAD

VIDEOS=(
  # LONG-FORM VIDEOS (newest first)
  # Format: "title_slug|https://www.youtube.com/watch?v=<VIDEO_ID>"
  # 
  # SHORTS
  # Format: "hormozi_short_<VIDEO_ID>|https://www.youtube.com/shorts/<VIDEO_ID>"
  #
)

LOG=ingest_m5.log
: > "$LOG"
total=${#VIDEOS[@]}

if (( total == 0 )); then
  echo "NO VIDEOS TO INGEST"
  exit 0
fi

i=0; ok=0; fail=0; skip=0

for entry in "${VIDEOS[@]}"; do
  i=$((i+1))
  slug="${entry%%|*}"; url="${entry#*|}"

  if gh api "repos/pranavgupta55/Scribe/contents/transcripts/${slug}.txt" >/dev/null 2>&1; then
    echo "[$i/$total] SKIP: $slug" | tee -a "$LOG"
    skip=$((skip+1)); continue
  fi

  echo "[$i/$total] START: $slug" | tee -a "$LOG"
  t0=$(date +%s)
  if bash scribe.sh "$url" "$slug" >>"$LOG" 2>&1; then
    dt=$(( $(date +%s) - t0 ))
    echo "[$i/$total] OK ($dt s): $slug" | tee -a "$LOG"
    ok=$((ok+1))

    meta="transcripts/${slug}.meta.json"
    if [[ -f "$meta" ]]; then
      python3 -c "
import json
p = '$meta'
d = json.load(open(p))
d['content_category'] = 'business_education'
d['creator'] = 'Alex Hormozi'
d['channel_handle'] = '@alexhormozi'
json.dump(d, open(p,'w'), indent=2)
"
    fi
  else
    echo "[$i/$total] FAIL: $slug" | tee -a "$LOG"
    fail=$((fail+1))
  fi
done

echo "======================================" | tee -a "$LOG"
echo "M5 Hormozi done: ok=$ok skip=$skip fail=$fail total=$total" | tee -a "$LOG"
```

Run: `chmod +x ingest_m5.sh && ./ingest_m5.sh`

Expected: ~12-14 h (varies by new video count & audio length)

---

## 6. Metadata format

Every `.meta.json` MUST have:

```json
{
  "content_category": "business_education",
  "creator": "Alex Hormozi",
  "channel_handle": "@alexhormozi"
}
```

---

## 7. Verify completion

```bash
tail -10 ingest_m5.log
```

Do NOT run `updateDB.sh` — that runs after ChromaDB rebuild task.

---

## 8. Constraints

- Do NOT modify scribe.sh, process.py, qwen_transcribe.py
- Do NOT run updateDB.sh
- Do NOT delete ~/Desktop/scribe-m5-hormozi until human confirms
- Do NOT hunt videos beyond @alexhormozi main channel
- If VIDEOS array is empty after step 3-4, do NOT invent videos

---

## Rollback

```bash
cd ~/Desktop/scribe-m5-hormozi
git reset --hard origin/main
```

---

## Estimation

- Audio: ~3-8 long-form (30-60 min each) + 10-30 Shorts (1-2 min each) = ~50-100 min total
- Transcription: ~10x realtime = ~500-1000 min (~8-17 h)
- With overhead: **12-14 h wall-clock**

Timeline: Start now → Done in 12-14 hours → Then run updateDB.sh --rebuild
