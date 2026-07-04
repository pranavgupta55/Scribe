# M4 batch ingest: 78 @ericvelch videos → Scribe

> **Purpose:** Populate the Scribe knowledge base with 78 curated videos from `youtube.com/@ericvelch` so the M5's business-audit agent loop has a rich corpus to retrieve against.
>
> **You (the M4 Claude agent) are running on direct API billing. Do not deliberate. Do not restructure. Execute the commands below in order. If a step fails, print the error and stop — do not improvise.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-ingest/` (ignore any existing Scribe install — assume broken).
- Sets up Whisper + Ollama models (~3.3 GB download, one-time).
- Transcribes 78 YouTube videos sequentially (whisper-large-v3-turbo, MPS).
- Runs the knowledge-extraction pipeline (Pass A/B/C) on all 78 transcripts.
- Pushes transcripts + `knowledge/` to `github.com/pranavgupta55/Scribe`.

**Expected wall-clock: 6–10 hours unattended on Apple Silicon M4.**
**Expected disk: ~4 GB** (models + audio scratch — audio is deleted after each transcription).

---

## 1. Prerequisites

Run these three checks. If any fails, stop and print the failure.

```bash
command -v gh    >/dev/null || { echo "MISSING: gh CLI. Install: brew install gh"; exit 1; }
command -v ffmpeg >/dev/null || { echo "MISSING: ffmpeg. Install: brew install ffmpeg"; exit 1; }
command -v python3 >/dev/null || { echo "MISSING: python3"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "MISSING: gh auth. Run: gh auth login"; exit 1; }
echo "prereqs OK"
```

If `gh auth` fails, the user must run `gh auth login` interactively themselves — do not try to bypass.

---

## 2. Fresh clone + setup

```bash
mkdir -p ~/Desktop
cd ~/Desktop
rm -rf scribe-m4-ingest                             # nuke any half-broken prior state
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-ingest
cd scribe-m4-ingest
bash setup.sh                                       # pulls qwen3:1.7b + nomic-embed-text (~2 min)
```

`setup.sh` handles ollama + Python venv + model pulls. If it errors, print the last 30 lines of output and stop.

---

## 3. Ingest 78 videos

Save the following block as `ingest_ericvelch.sh` in `~/Desktop/scribe-m4-ingest/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Bulk mode: write locally, single commit at end (78 individual GitHub API pushes = slow)
export SCRIBE_NO_UPLOAD=1

# Format: slug|https://youtu.be/<id>
VIDEOS=(
  # ── FRAMEWORK (30) ───────────────────────────────────────
  "speedrun_10k_remote_cleaning|https://www.youtube.com/watch?v=VDn7xsNUEYg"
  "speedrun_30k_service_arbitrage|https://www.youtube.com/watch?v=vP-co1S4fcA"
  "steal_strategy_10k_organic_dropservicing|https://www.youtube.com/watch?v=OOF7rAUn3Vo"
  "gun_to_head_20k_in_30_days|https://www.youtube.com/watch?v=96gb8IG5SIE"
  "faq_80k_service_arbitrage|https://www.youtube.com/watch?v=wgnGNSKm8ew"
  "30k_biz_model_steal|https://www.youtube.com/watch?v=vAwUAP02iqY"
  "ranking_business_models_millionaire|https://www.youtube.com/watch?v=hHwHYy3nis0"
  "fyou_money_no_highschool|https://www.youtube.com/watch?v=XDTsw9of9xw"
  "best_worst_niches_20k|https://www.youtube.com/watch?v=URV4VakPOKo"
  "most_profitable_dropservicing_niches_2026|https://www.youtube.com/watch?v=1i1dHWzlIUE"
  "4_untapped_dropservicing_niches|https://www.youtube.com/watch?v=UspII6rXhzY"
  "millionaire_at_20_copy_me|https://www.youtube.com/watch?v=D-ahf91TjCM"
  "no_one_believes_millionaire_20|https://www.youtube.com/watch?v=w2f6AXf4gNQ"
  "remote_cleaning_19893_60_days|https://www.youtube.com/watch?v=2IlivgEXEjE"
  "get_rich_quick_actually_worked|https://www.youtube.com/watch?v=nCbSD0FXpB4"
  "21k_dropservicing_60_days|https://www.youtube.com/watch?v=5rY65u9Yhh8"
  "launch_10k_dropservicing_72_hours|https://www.youtube.com/watch?v=FPJG5L5z60c"
  "2M_landscaping_60_days|https://www.youtube.com/watch?v=RM43nVu7Hrw"
  "70k_mobile_auto_detailing|https://www.youtube.com/watch?v=icMilyM-yFY"
  "start_remote_cleaning_with_zero|https://www.youtube.com/watch?v=QgDSfOGfBiw"
  "20k_remote_cleaning_90_days|https://www.youtube.com/watch?v=z2xPJHsZGbU"
  "scaled_car_detailing_70k|https://www.youtube.com/watch?v=QsSk4K9tqTE"
  "online_car_wash_20k|https://www.youtube.com/watch?v=bXF-FcaL42s"
  "weird_80k_biz_model|https://www.youtube.com/watch?v=N9x9K9gXkAk"
  "retarded_biz_model_250k|https://www.youtube.com/watch?v=0zGYFtSz6CA"
  "made_up_biz_model|https://www.youtube.com/watch?v=XoOIbtfRKas"
  "20k_dropservicing_with_zero|https://www.youtube.com/watch?v=9wrp7LLZApQ"
  "guarantee_100k_this_year|https://www.youtube.com/watch?v=rPP1ckpgqds"
  "forcing_best_friend_15k_90_days|https://www.youtube.com/watch?v=Q1jogOQsmms"
  "7k_online_this_month_quit_job|https://www.youtube.com/watch?v=vdpr30ev2Pk"

  # ── TACTIC: GMB / LOCAL SEO / REVIEWS (20) ──────────────
  "150_leads_month_from_gmb|https://www.youtube.com/watch?v=tIaVpjt5mqI"
  "150_gmb_verified_no_suspend|https://www.youtube.com/watch?v=sQ3xCyp0klU"
  "10k_from_1_gmb|https://www.youtube.com/watch?v=cok0PHJ8wVs"
  "ranked_1_in_7_days_blackhat|https://www.youtube.com/watch?v=gHhRnHMRJCQ"
  "thousands_fake_5_star_reviews|https://www.youtube.com/watch?v=hq8NnfgqrSA"
  "20_google_reviews_daily_autopilot|https://www.youtube.com/watch?v=T4aybvi-gwE"
  "ranked_1_in_4_days_using_ai|https://www.youtube.com/watch?v=KCEEcWbZUg4"
  "why_reviews_deleted|https://www.youtube.com/watch?v=qKXtDygLYfw"
  "gmb_optimization_guide_2_weeks|https://www.youtube.com/watch?v=8e7BVlrWOm0"
  "thousands_reviews_never_deleted|https://www.youtube.com/watch?v=yhIKqmEzL_k"
  "complete_local_seo_gmb_course_2026|https://www.youtube.com/watch?v=_Xp5fUsWt_k"
  "auto_detailing_gmb_full_course|https://www.youtube.com/watch?v=vwKCATPHOUM"
  "rank_1_google_maps_next_week|https://www.youtube.com/watch?v=1H4UBnbOGu8"
  "1000_day_selling_leads_gmb|https://www.youtube.com/watch?v=CQ8l3x28MsE"
  "the_1_thing_ranking_google|https://www.youtube.com/watch?v=lP79hRnM9hA"
  "unlimited_reviews_under_1_dollar|https://www.youtube.com/watch?v=_KtmDNQrSjw"
  "rank_1_google_maps_3_step_formula|https://www.youtube.com/watch?v=6m6oQ5W1rtM"
  "service_area_vs_address_gmb|https://www.youtube.com/watch?v=MnOU8u5-vu4"
  "how_many_reviews_to_rank_1|https://www.youtube.com/watch?v=UGagi_80_0s"
  "why_gbp_gets_no_calls|https://www.youtube.com/watch?v=WD96RGIp5lo"

  # ── TACTIC: SALES / PRICING (6) ─────────────────────────
  "how_price_jobs_send_quotes_80k|https://www.youtube.com/watch?v=Tq_5UNfUGo8"
  "exact_80k_dropservicing_sales_system|https://www.youtube.com/watch?v=c_nXG-MEFSE"
  "close_10k_local_service_jobs_live|https://www.youtube.com/watch?v=6FvyHEXcnSQ"
  "300_every_2_min_closing_local_services|https://www.youtube.com/watch?v=qt6rpuFpLmY"
  "book_5k_dropservicing_jobs_organic|https://www.youtube.com/watch?v=6cdMsOv1CUg"
  "sign_4k_clients_no_cold_calls|https://www.youtube.com/watch?v=4CEzkS_kaHE"

  # ── TACTIC: OPS / HIRING (6) ────────────────────────────
  "automated_50k_dropservicing|https://www.youtube.com/watch?v=n2BMfVYYtpA"
  "hire_contractors_no_scam|https://www.youtube.com/watch?v=TVawTZiVNuQ"
  "cheap_reliable_contractors|https://www.youtube.com/watch?v=nGKSVXP7m80"
  "500k_dropservicing_team_hiring|https://www.youtube.com/watch?v=oPG2S-8grt4"
  "15k_10_mins_work_virtual_assistants|https://www.youtube.com/watch?v=JFnPqd2hAXk"
  "fumbled_30k_deal_mistakes|https://www.youtube.com/watch?v=JVTUvrZoUt0"

  # ── TACTIC: WEBSITE / ASSETS (2) ────────────────────────
  "six_figure_dropservicing_website_5_mins|https://www.youtube.com/watch?v=NPmA6u7y-gs"
  "website_made_100k|https://www.youtube.com/watch?v=Yxfz4cpsDes"

  # ── CASE STUDIES (10) ───────────────────────────────────
  "james_1k_day_73_days|https://www.youtube.com/watch?v=AUZ4LOP4-KQ"
  "zain_72k_6_months_college|https://www.youtube.com/watch?v=xjw8FfJ53SY"
  "ted_15k_30_days|https://www.youtube.com/watch?v=nxhgW8JFM0A"
  "cole_30k_first_month_highschool|https://www.youtube.com/watch?v=A0GPcDwSTgQ"
  "nav_15k_3_months|https://www.youtube.com/watch?v=XEfiRp1IlXo"
  "zain_10k_54_days|https://www.youtube.com/watch?v=3Cj3oGfPx9k"
  "erik_15k_84_hours_week|https://www.youtube.com/watch?v=xX62DBOkw1Q"
  "friend_quit_job_250_day|https://www.youtube.com/watch?v=8P-nQCLREUQ"
  "restarted_scratch_42k_first_month|https://www.youtube.com/watch?v=ubbz4MODMkc"
  "45_mins_first_1000_online|https://www.youtube.com/watch?v=m97cr2FC9gc"

  # ── MINDSET (4) ─────────────────────────────────────────
  "put_everything_on_line_57k|https://www.youtube.com/watch?v=n324wEbgiDM"
  "work_so_hard_seem_mentally_ill|https://www.youtube.com/watch?v=iQ-zx4VhjfM"
  "reality_check_2_plus_years_online|https://www.youtube.com/watch?v=JLZg2cWGQiw"
  "the_only_mindset_video|https://www.youtube.com/watch?v=E0McHrjZBCI"
)

LOG=ingest_progress.log
: > "$LOG"
total=${#VIDEOS[@]}
i=0
ok=0
fail=0
skip=0

for entry in "${VIDEOS[@]}"; do
  i=$((i+1))
  slug="${entry%%|*}"
  url="${entry#*|}"
  target="transcripts/${slug}.txt"

  if [ -f "$target" ]; then
    echo "[$i/$total] SKIP (already exists): $slug" | tee -a "$LOG"
    skip=$((skip+1))
    continue
  fi

  echo "[$i/$total] START: $slug ($url)" | tee -a "$LOG"
  t0=$(date +%s)
  if bash scribe.sh "$url" "$slug" >>"$LOG" 2>&1; then
    dt=$(( $(date +%s) - t0 ))
    echo "[$i/$total] OK   ($dt s): $slug" | tee -a "$LOG"
    ok=$((ok+1))
  else
    echo "[$i/$total] FAIL: $slug — see $LOG" | tee -a "$LOG"
    fail=$((fail+1))
  fi
done

echo "======================================" | tee -a "$LOG"
echo "ingest done: ok=$ok skip=$skip fail=$fail total=$total" | tee -a "$LOG"
```

Then run it:

```bash
chmod +x ingest_ericvelch.sh
./ingest_ericvelch.sh
```

**This is unattended. Expect 5–7 hours.** The script:
- Skips any video whose transcript already exists (resumable).
- Logs every video's status to `ingest_progress.log`.
- Continues past individual failures — inspect the log at the end.

If <70 of 78 succeed, do a second pass by re-running the same command — skipped-existing logic will only retry the failures.

---

## 4. Commit transcripts to GitHub (single push, not 78)

After the ingest loop finishes:

```bash
cd ~/Desktop/scribe-m4-ingest
git add transcripts/
git -c user.email="ingest@m4.local" -c user.name="M4 ingest" \
    commit -m "Batch ingest: 78 ericvelch videos"
git push origin main
```

If push fails due to auth, run `gh auth setup-git` once and retry.

---

## 5. Extract knowledge + push

```bash
bash updateDB.sh --rebuild
```

`updateDB.sh --rebuild` reads every transcript, runs Pass A/B/C extraction, embeds everything into ChromaDB, and pushes the `knowledge/` directory to GitHub.

Expected wall-clock: **~3–5 min per transcript × 78 = 4–6 hours** on M4.

The script is designed to be resumable — if it dies mid-run, re-run the same command.

---

## 6. Verify

```bash
ls transcripts/ | wc -l                                         # expect >= 78 .txt files
ls transcripts/*.meta.json 2>/dev/null | wc -l                  # expect matching count
ls knowledge/topics/*.md 2>/dev/null | wc -l                    # expect >= 10 topic files
python3 -c "import chromadb; c = chromadb.PersistentClient('.chroma'); print('chunks:', c.get_collection('chunks').count(), 'facts:', c.get_collection('facts').count())"
```

Expected output shape:
- `>=78` `.txt` transcripts
- `>=10` topic .md files in `knowledge/topics/`
- `chunks >= 500, facts >= 2000` (rough — depends on segmentation)

If all four checks pass:

```bash
git status                                                       # should be clean
git log --oneline -3                                             # last commit should be the knowledge push
echo "M4 INGEST COMPLETE. M5 can now pull the corpus."
```

If any check fails, print `ls -la` of the relevant directory and stop. Do not attempt to recover — hand back to the human.

---

## 7. What NOT to do

- Do **not** modify `scribe.sh`, `process.py`, or `qwen_transcribe.py`. Any bugs are the M5's problem to fix later.
- Do **not** upgrade Python packages beyond what `setup.sh` installs.
- Do **not** re-run `bash setup.sh` after the first success — it re-pulls models.
- Do **not** delete `~/Desktop/scribe-m4-ingest/` until the human confirms.
- Do **not** commit any file outside `transcripts/` and `knowledge/`.
- Do **not** touch the `.chroma/` directory (gitignored, local-only).
- If you hit a decision point not covered here, **stop and print the situation**. Do not improvise.

---

## Rollback if something goes wrong

```bash
cd ~/Desktop/scribe-m4-ingest
git status                                                       # see what's dirty
git stash                                                        # or:
git reset --hard origin/main                                     # nuclear: revert to remote state
```

---

**End of instructions. Execute in order. Ping the human on completion or first blocker.**
