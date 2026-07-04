# M4 batch ingest: Greg LaVecchia (Bloom Nutrition) → Scribe

> **Purpose:** M4 transcribes the full Greg LaVecchia corpus (~14 confirmed videos — see scope note) into Scribe. Single-machine run, no split. Complements the earlier Ericvelch ingest with 8-figure DTC brand-building content for Atlas.
>
> **You (the M4 Claude agent) are on direct API billing. Do not deliberate. Do not restructure. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-greg/` (any existing Scribe install is assumed broken).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time; skipped if models already present).
- Transcribes ~14 videos serially on M4 (~30 min wall-clock — corpus is small, no split needed).
- Uploads each transcript + `.meta.json` sidecar directly to `github.com/pranavgupta55/Scribe` via GitHub API (atomic per-file push to `origin/main`, no PR).
- **Does NOT run `updateDB.sh`** — knowledge extraction is a separate step run manually after M4 finishes.

**Expected wall-clock: ~35 min unattended.**

---

## SCOPE NOTE — READ THIS FIRST

Greg LaVecchia's own channel `@GregLaVecchia` is small. A prior curation pass found **only ~10 long-form videos enumerable via web search**; most of his substantive business teaching lives in **podcast appearances on other channels** (Built For More, Sweat Equity, Pursuit of Wellness, etc.). The user has confirmed to proceed with the smaller corpus.

- **Do NOT hunt for 100+ videos.** They don't exist.
- Podcast-appearance videos are prefixed `podcast_` in the slug and marked in `.meta.json`.
- If YouTube channel enumeration via yt-dlp reveals more of his own uploads at ingest time, append them to `VIDEOS` in `ingest_greg.sh` — but do not block on it.

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
rm -rf scribe-m4-greg
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-greg
cd scribe-m4-greg
bash setup.sh
```

`setup.sh` pulls ollama models (~2 min if fresh; instant if already present). If it errors, print the last 30 lines and stop.

---

## 3. URL → slug mapping (14 videos)

| # | Slug | Type | URL |
|---|------|------|-----|
| 1 | `bloom_playbook_2026` | long-form (own ch.) | https://www.youtube.com/watch?v=qFeOhqiQfCk |
| 2 | `500m_ceo_day` | long-form (own ch.) | https://www.youtube.com/watch?v=XB7znyqxiBk |
| 3 | `1b_brand_20s` | long-form (own ch.) | https://www.youtube.com/watch?v=ut2X6SC84bA |
| 4 | `billion_brand_1rm_bench` | long-form (own ch.) | https://www.youtube.com/watch?v=6SR6dojqQ1M |
| 5 | `bloom_hottest_ones` | long-form (own ch.) | https://www.youtube.com/watch?v=tKmUeDbfrUk |
| 6 | `podcast_built_for_more_billion_dollar` | podcast | https://www.youtube.com/watch?v=Cmgbv5p75bc |
| 7 | `podcast_sweat_equity_500m_65min` | podcast | https://www.youtube.com/watch?v=Zk9smI4OJSQ |
| 8 | `podcast_9fig_supplement_brand` | podcast | https://www.youtube.com/watch?v=PSBtNCEbcdQ |
| 9 | `podcast_2p5_gpa_1b_brand` | podcast | https://www.youtube.com/watch?v=FBEV4da-v10 |
| 10 | `podcast_brand_sells_out_before_launch` | podcast | https://www.youtube.com/watch?v=JhKZB-rGGuw |
| 11 | `podcast_bloom_sparkling_growth` | podcast | https://www.youtube.com/watch?v=hp5uxvJk2DU |
| 12 | `podcast_foundermade_review` | podcast (short) | https://www.youtube.com/watch?v=8Kt28Pl9QEY |
| 13 | `short_who_cares_lose_everything` | short | https://www.youtube.com/shorts/zqSFJr4-ZaA |
| 14 | `short_day_in_life_entrepreneur` | short | https://www.youtube.com/shorts/_JVctgaB3kE |

---

## 4. Ingest script

Save as `ingest_greg.sh` in `~/Desktop/scribe-m4-greg/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Each transcript pushes to GitHub as its own commit (atomic per-file).
unset SCRIBE_NO_UPLOAD

# slug|url — longest first for early-signal debugging
VIDEOS=(
  "podcast_built_for_more_billion_dollar|https://www.youtube.com/watch?v=Cmgbv5p75bc"
  "podcast_sweat_equity_500m_65min|https://www.youtube.com/watch?v=Zk9smI4OJSQ"
  "podcast_9fig_supplement_brand|https://www.youtube.com/watch?v=PSBtNCEbcdQ"
  "podcast_2p5_gpa_1b_brand|https://www.youtube.com/watch?v=FBEV4da-v10"
  "podcast_brand_sells_out_before_launch|https://www.youtube.com/watch?v=JhKZB-rGGuw"
  "podcast_bloom_sparkling_growth|https://www.youtube.com/watch?v=hp5uxvJk2DU"
  "bloom_playbook_2026|https://www.youtube.com/watch?v=qFeOhqiQfCk"
  "500m_ceo_day|https://www.youtube.com/watch?v=XB7znyqxiBk"
  "1b_brand_20s|https://www.youtube.com/watch?v=ut2X6SC84bA"
  "billion_brand_1rm_bench|https://www.youtube.com/watch?v=6SR6dojqQ1M"
  "bloom_hottest_ones|https://www.youtube.com/watch?v=tKmUeDbfrUk"
  "podcast_foundermade_review|https://www.youtube.com/watch?v=8Kt28Pl9QEY"
  "short_who_cares_lose_everything|https://www.youtube.com/shorts/zqSFJr4-ZaA"
  "short_day_in_life_entrepreneur|https://www.youtube.com/shorts/_JVctgaB3kE"
)

LOG=ingest_greg.log
: > "$LOG"
total=${#VIDEOS[@]}
i=0; ok=0; fail=0; skip=0

for entry in "${VIDEOS[@]}"; do
  i=$((i+1))
  slug="${entry%%|*}"; url="${entry#*|}"

  # Skip if transcript already exists on remote (resume-safe)
  if gh api "repos/pranavgupta55/Scribe/contents/transcripts/${slug}.txt" >/dev/null 2>&1; then
    echo "[$i/$total] SKIP (exists on remote): $slug" | tee -a "$LOG"
    skip=$((skip+1)); continue
  fi

  echo "[$i/$total] START: $slug" | tee -a "$LOG"
  t0=$(date +%s)
  if bash scribe.sh "$url" "$slug" >>"$LOG" 2>&1; then
    dt=$(( $(date +%s) - t0 ))
    echo "[$i/$total] OK ($dt s): $slug" | tee -a "$LOG"
    ok=$((ok+1))
  else
    echo "[$i/$total] FAIL: $slug — see $LOG tail" | tee -a "$LOG"
    fail=$((fail+1))
  fi
done

echo "======================================" | tee -a "$LOG"
echo "M4 Greg done: ok=$ok skip=$skip fail=$fail total=$total" | tee -a "$LOG"
```

Then run:

```bash
chmod +x ingest_greg.sh
./ingest_greg.sh
```

Unattended. ~30 min. Each transcript + `.meta.json` uploads to GitHub as its own commit directly to `origin/main` (no PR).

---

## 5. `.meta.json` sidecar — content category

Every `.meta.json` sidecar written by `scribe.sh` for this batch MUST carry:

```json
{
  "content_category": "dtc_brand_building",
  "creator": "Greg LaVecchia",
  "channel_handle": "@GregLaVecchia",
  "brand": "Bloom Nutrition"
}
```

This differentiates the corpus from the Ericvelch batch (`service_business_lead_gen`) at Atlas retrieval time.

If `scribe.sh` doesn't accept a category flag natively, patch the sidecar after each successful transcript:

```bash
# in ingest_greg.sh, after the OK branch:
meta="transcripts/${slug}.meta.json"
if [[ -f "$meta" ]]; then
  python3 -c "
import json, sys
p = '$meta'
d = json.load(open(p))
d['content_category'] = 'dtc_brand_building'
d['creator'] = 'Greg LaVecchia'
d['channel_handle'] = '@GregLaVecchia'
d['brand'] = 'Bloom Nutrition'
json.dump(d, open(p,'w'), indent=2)
"
fi
```

---

## 6. Verify + notify

```bash
tail -5 ingest_greg.log
gh api repos/pranavgupta55/Scribe/contents/transcripts --jq '[.[] | select(.name | startswith("bloom_") or startswith("podcast_") or startswith("500m_") or startswith("1b_") or startswith("billion_") or startswith("short_"))] | length'
```

If `ok >= 12` (of 14) print `M4 GREG INGEST COMPLETE` and stop. If `ok < 12`, print the failing slugs and stop.

**Do NOT run `updateDB.sh`.** Knowledge extraction runs separately after this batch is confirmed complete.

---

## 7. What NOT to do

- Do **not** modify `scribe.sh`, `process.py`, or `qwen_transcribe.py`.
- Do **not** run `updateDB.sh`.
- Do **not** commit anything outside `transcripts/`.
- Do **not** delete `~/Desktop/scribe-m4-greg/` until the human confirms.
- Do **not** hunt for videos beyond the 14 in the table above unless a `yt-dlp --flat-playlist https://www.youtube.com/@GregLaVecchia/videos` dump surfaces new IDs — the corpus is genuinely small.
- If you hit a decision point not covered here, **stop and print the situation**.

---

## Rollback

```bash
cd ~/Desktop/scribe-m4-greg
git status
git reset --hard origin/main    # nuclear — reverts to remote
```

---

**End of instructions. Execute in order. Ping the human on completion or first blocker.**
