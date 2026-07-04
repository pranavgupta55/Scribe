# M4 batch ingest: 25 @ericvelch videos → Scribe (parallel with M5)

> **Purpose:** M4 transcribes its 25-video share of a 51-video @ericvelch corpus. M5 (the other laptop) is running its own 26-video share in parallel. Combined, both finish in ~60 min wall-clock.
>
> **You (the M4 Claude agent) are on direct API billing. Do not deliberate. Do not restructure. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-ingest/` (any existing Scribe install is assumed broken).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time).
- Transcribes the 25 assigned videos in parallel with M5 (~52 min transcription).
- Uploads each transcript directly to `github.com/pranavgupta55/Scribe` via GitHub API (idempotent upsert — safe to run against M5's in-flight commits).
- **Does NOT run knowledge extraction** — the M5 handles that once both machines finish.

**Expected wall-clock: ~55 min unattended** (2 min setup + 52 min transcription + 1 min buffer).

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
rm -rf scribe-m4-ingest
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-ingest
cd scribe-m4-ingest
bash setup.sh
```

`setup.sh` pulls ollama models (~2 min). If it errors, print the last 30 lines and stop.

---

## 3. Ingest 25 videos (M4's share)

Save as `ingest_m4.sh` in `~/Desktop/scribe-m4-ingest/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Default upload mode: each transcript pushes to GitHub as its own commit.
# M5 is doing the same for a disjoint set of files — no conflicts (unique slugs).
unset SCRIBE_NO_UPLOAD

# slug|url — M4's assigned 25 videos (longest first for load balance)
VIDEOS=(
  "7k_online_this_month_quit_job|https://www.youtube.com/watch?v=vdpr30ev2Pk"
  "2M_landscaping_60_days|https://www.youtube.com/watch?v=RM43nVu7Hrw"
  "online_car_wash_20k|https://www.youtube.com/watch?v=bXF-FcaL42s"
  "10k_from_1_gmb|https://www.youtube.com/watch?v=cok0PHJ8wVs"
  "fumbled_30k_deal_mistakes|https://www.youtube.com/watch?v=JVTUvrZoUt0"
  "ranking_business_models_millionaire|https://www.youtube.com/watch?v=hHwHYy3nis0"
  "made_up_biz_model|https://www.youtube.com/watch?v=XoOIbtfRKas"
  "20k_dropservicing_with_zero|https://www.youtube.com/watch?v=9wrp7LLZApQ"
  "300_every_2_min_closing_local_services|https://www.youtube.com/watch?v=qt6rpuFpLmY"
  "guarantee_100k_this_year|https://www.youtube.com/watch?v=rPP1ckpgqds"
  "forcing_best_friend_15k_90_days|https://www.youtube.com/watch?v=Q1jogOQsmms"
  "15k_10_mins_work_virtual_assistants|https://www.youtube.com/watch?v=JFnPqd2hAXk"
  "rank_1_google_maps_3_step_formula|https://www.youtube.com/watch?v=6m6oQ5W1rtM"
  "service_area_vs_address_gmb|https://www.youtube.com/watch?v=MnOU8u5-vu4"
  "ted_15k_30_days|https://www.youtube.com/watch?v=nxhgW8JFM0A"
  "thousands_reviews_never_deleted|https://www.youtube.com/watch?v=yhIKqmEzL_k"
  "500k_dropservicing_team_hiring|https://www.youtube.com/watch?v=oPG2S-8grt4"
  "erik_15k_84_hours_week|https://www.youtube.com/watch?v=xX62DBOkw1Q"
  "nav_15k_3_months|https://www.youtube.com/watch?v=XEfiRp1IlXo"
  "why_gbp_gets_no_calls|https://www.youtube.com/watch?v=WD96RGIp5lo"
  "how_many_reviews_to_rank_1|https://www.youtube.com/watch?v=UGagi_80_0s"
  "ranked_1_in_7_days_blackhat|https://www.youtube.com/watch?v=gHhRnHMRJCQ"
  "rank_1_google_maps_next_week|https://www.youtube.com/watch?v=1H4UBnbOGu8"
  "six_figure_dropservicing_website_5_mins|https://www.youtube.com/watch?v=NPmA6u7y-gs"
  "150_gmb_verified_no_suspend|https://www.youtube.com/watch?v=sQ3xCyp0klU"
)

LOG=ingest_m4.log
: > "$LOG"
total=${#VIDEOS[@]}
i=0; ok=0; fail=0; skip=0

for entry in "${VIDEOS[@]}"; do
  i=$((i+1))
  slug="${entry%%|*}"; url="${entry#*|}"

  # Check if the transcript already exists on the remote (M5 might have raced us, or we're resuming)
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
echo "M4 done: ok=$ok skip=$skip fail=$fail total=$total" | tee -a "$LOG"
```

Then run:

```bash
chmod +x ingest_m4.sh
./ingest_m4.sh
```

Unattended. ~52 min. Each transcript uploads to GitHub as its own commit — no conflicts with M5 because file paths are disjoint.

---

## 4. Verify + notify

```bash
tail -5 ingest_m4.log
gh api repos/pranavgupta55/Scribe/contents/transcripts --jq '. | length'   # total transcripts on remote (M4 + M5's shares combined once both done)
```

If `ok >= 23` (of 25) print `M4 INGEST COMPLETE` and stop. If `ok < 23`, print the failing slugs and stop.

**Do NOT run `updateDB.sh`.** M5 handles knowledge extraction once both machines confirm done.

---

## 5. What NOT to do

- Do **not** modify `scribe.sh`, `process.py`, or `qwen_transcribe.py`.
- Do **not** run `updateDB.sh` — that's M5's job after both machines finish.
- Do **not** commit anything outside `transcripts/`.
- Do **not** delete `~/Desktop/scribe-m4-ingest/` until the human confirms.
- If you hit a decision point not covered here, **stop and print the situation**.

---

## Rollback

```bash
cd ~/Desktop/scribe-m4-ingest
git status
git reset --hard origin/main    # nuclear — reverts to remote
```

---

**End of instructions. Execute in order. Ping the human on completion or first blocker.**
