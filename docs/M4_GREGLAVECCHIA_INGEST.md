# M4 batch ingest: Greg LaVecchia (Bloom Nutrition) → Scribe

> **Purpose:** M4 transcribes the full `@GregLaVecchia` corpus (21 long-form + 99 Shorts = 120 videos, verified via `yt-dlp --flat-playlist`) into Scribe. Single-machine run, no split. Complements the earlier Ericvelch ingest with 8-figure DTC brand-building content for Atlas.
>
> **You (the M4 Claude agent) are on direct API billing. Do not deliberate. Do not restructure. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-greg/` (any existing Scribe install is assumed broken).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time; skipped if models already present).
- Transcribes 120 videos serially on M4 (~1-2 h wall-clock: ~7.8 h of audio for long-form + ~50 min of Shorts, whisper runs ~10× realtime on M4 GPU).
- Uploads each transcript + `.meta.json` sidecar directly to `github.com/pranavgupta55/Scribe` via GitHub API (atomic per-file push to `origin/main`, no PR).
- **Does NOT run `updateDB.sh`** — knowledge extraction is a separate step run manually after M4 finishes.

**Expected wall-clock: ~1-2 h unattended. Runs comfortably overnight.**

---

## SCOPE NOTE — READ THIS FIRST

The full `@GregLaVecchia` channel is: **21 long-form videos + 99 Shorts = 120 total.**

The Shorts are largely clipped from the long-form content (same teachings, shorter phrasing) — they add corpus density and per-clip metadata but are low-signal individually. Include them all: they still contribute embeddings and quotable snippets at retrieval time.

Podcast appearances on OTHER channels (Built For More, Sweat Equity, etc.) are OUT of scope for this batch. If you want them later they can be added in a separate ingest.

---

## 1. Prerequisites

```bash
command -v gh      >/dev/null || { echo "MISSING: gh CLI (brew install gh)"; exit 1; }
command -v ffmpeg  >/dev/null || { echo "MISSING: ffmpeg (brew install ffmpeg)"; exit 1; }
command -v python3 >/dev/null || { echo "MISSING: python3"; exit 1; }
command -v yt-dlp  >/dev/null || { echo "MISSING: yt-dlp (pip3 install -U yt-dlp)"; exit 1; }
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

## 3. URL → slug mapping (long-form only; Shorts use `greg_short_<video_id>`)

| # | Slug | Duration | URL |
|---|------|---------|-----|
| 1 | `ten_yrs_brutally_honest_brand_advice` | 31:49 | https://www.youtube.com/watch?v=9NTawfl1Rko |
| 2 | `before_chase_success` | 29:31 | https://www.youtube.com/watch?v=XPYdYNIpZRk |
| 3 | `unlocking_huge_opportunity` | 20:09 | https://www.youtube.com/watch?v=7XREijH-qIo |
| 4 | `best_friend_billion_dollar_playbook` | 34:37 | https://www.youtube.com/watch?v=ZlepS1Uz5WU |
| 5 | `ten_yrs_mistakes_38_min` | 38:42 | https://www.youtube.com/watch?v=owLvP0qF42U |
| 6 | `168_hrs_decisions_unseen` | 39:32 | https://www.youtube.com/watch?v=qm6kliODG8M |
| 7 | `undisciplined_want_success` | 18:55 | https://www.youtube.com/watch?v=CjLW6fCMDKY |
| 8 | `1b_number_1_tiktok_shop` | 16:17 | https://www.youtube.com/watch?v=ORKBX1r5-zI |
| 9 | `how_millionaires_think_diff` | 18:50 | https://www.youtube.com/watch?v=K_bUFFSOON8 |
| 10 | `1b_6_decisions_20s` | 13:12 | https://www.youtube.com/watch?v=6a8wGgWx3EY |
| 11 | `500m_watch_dealer` | 29:12 | https://www.youtube.com/watch?v=ZIU4SDu4gHQ |
| 12 | `launch_product_no_following` | 29:44 | https://www.youtube.com/watch?v=6xO3NP8x9Y0 |
| 13 | `built_1b_brand_20s` | 30:20 | https://www.youtube.com/watch?v=ut2X6SC84bA |
| 14 | `raw_founder_conversations` | 23:08 | https://www.youtube.com/watch?v=pZhqgJ9wILE |
| 15 | `1b_100m_deals_la_show` | 16:56 | https://www.youtube.com/watch?v=rcLpq5Ikv7c |
| 16 | `two_gen_tiktok_millionaires` | 27:25 | https://www.youtube.com/watch?v=YNrI2juyNK0 |
| 17 | `1b_secret_city_deals` | 11:29 | https://www.youtube.com/watch?v=DPdvIjUkqrc |
| 18 | `promised_myself_own_this` | 5:21 | https://www.youtube.com/watch?v=yW6S3b0JG4A |
| 19 | `bloom_ceo_home_gym` | 14:26 | https://www.youtube.com/watch?v=fGlDZCnPDqE |
| 20 | `million_dollar_convos_serial` | 14:35 | https://www.youtube.com/watch?v=TbYdgft3GaI |
| 21 | `bloom_ceo_did_it_why_not_you` | 16:45 | https://www.youtube.com/watch?v=Ik4C4SSseTw |

**Shorts:** All 99 Shorts are included with slug pattern `greg_short_<video_id>` (see `ingest_greg.sh` below — the array is exhaustive).

---

## 4. Ingest script

Save as `ingest_greg.sh` in `~/Desktop/scribe-m4-greg/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Each transcript pushes to GitHub as its own commit (atomic per-file).
unset SCRIBE_NO_UPLOAD

# slug|url — long-form first (higher signal), then Shorts
VIDEOS=(
  # ── 21 long-form (verified via yt-dlp --flat-playlist) ──────────────
  "ten_yrs_brutally_honest_brand_advice|https://www.youtube.com/watch?v=9NTawfl1Rko"
  "before_chase_success|https://www.youtube.com/watch?v=XPYdYNIpZRk"
  "unlocking_huge_opportunity|https://www.youtube.com/watch?v=7XREijH-qIo"
  "best_friend_billion_dollar_playbook|https://www.youtube.com/watch?v=ZlepS1Uz5WU"
  "ten_yrs_mistakes_38_min|https://www.youtube.com/watch?v=owLvP0qF42U"
  "168_hrs_decisions_unseen|https://www.youtube.com/watch?v=qm6kliODG8M"
  "undisciplined_want_success|https://www.youtube.com/watch?v=CjLW6fCMDKY"
  "1b_number_1_tiktok_shop|https://www.youtube.com/watch?v=ORKBX1r5-zI"
  "how_millionaires_think_diff|https://www.youtube.com/watch?v=K_bUFFSOON8"
  "1b_6_decisions_20s|https://www.youtube.com/watch?v=6a8wGgWx3EY"
  "500m_watch_dealer|https://www.youtube.com/watch?v=ZIU4SDu4gHQ"
  "launch_product_no_following|https://www.youtube.com/watch?v=6xO3NP8x9Y0"
  "built_1b_brand_20s|https://www.youtube.com/watch?v=ut2X6SC84bA"
  "raw_founder_conversations|https://www.youtube.com/watch?v=pZhqgJ9wILE"
  "1b_100m_deals_la_show|https://www.youtube.com/watch?v=rcLpq5Ikv7c"
  "two_gen_tiktok_millionaires|https://www.youtube.com/watch?v=YNrI2juyNK0"
  "1b_secret_city_deals|https://www.youtube.com/watch?v=DPdvIjUkqrc"
  "promised_myself_own_this|https://www.youtube.com/watch?v=yW6S3b0JG4A"
  "bloom_ceo_home_gym|https://www.youtube.com/watch?v=fGlDZCnPDqE"
  "million_dollar_convos_serial|https://www.youtube.com/watch?v=TbYdgft3GaI"
  "bloom_ceo_did_it_why_not_you|https://www.youtube.com/watch?v=Ik4C4SSseTw"

  # ── 99 Shorts (verified via yt-dlp --flat-playlist /shorts) ─────────
  "greg_short_e29Oo0Z272k|https://www.youtube.com/shorts/e29Oo0Z272k"
  "greg_short_rv5k08GP4Kg|https://www.youtube.com/shorts/rv5k08GP4Kg"
  "greg_short_nGMZxnpGy5E|https://www.youtube.com/shorts/nGMZxnpGy5E"
  "greg_short_gSWZHqiZNJo|https://www.youtube.com/shorts/gSWZHqiZNJo"
  "greg_short_cIc-VfgZx9I|https://www.youtube.com/shorts/cIc-VfgZx9I"
  "greg_short_mnYIUY4xIyc|https://www.youtube.com/shorts/mnYIUY4xIyc"
  "greg_short_s_r1mrEF44E|https://www.youtube.com/shorts/s_r1mrEF44E"
  "greg_short_bJCAn8ygAjU|https://www.youtube.com/shorts/bJCAn8ygAjU"
  "greg_short_jMZSF9tcUj4|https://www.youtube.com/shorts/jMZSF9tcUj4"
  "greg_short_gfaVpfPdzUw|https://www.youtube.com/shorts/gfaVpfPdzUw"
  "greg_short_zXNeGzA02kE|https://www.youtube.com/shorts/zXNeGzA02kE"
  "greg_short_v3SmyKSvkx0|https://www.youtube.com/shorts/v3SmyKSvkx0"
  "greg_short_s_6bbnJdQJA|https://www.youtube.com/shorts/s_6bbnJdQJA"
  "greg_short_q5wBxKeN5Lg|https://www.youtube.com/shorts/q5wBxKeN5Lg"
  "greg_short_0rEypHI49zY|https://www.youtube.com/shorts/0rEypHI49zY"
  "greg_short_YaiJlkypFB4|https://www.youtube.com/shorts/YaiJlkypFB4"
  "greg_short_nNBzvnAOTng|https://www.youtube.com/shorts/nNBzvnAOTng"
  "greg_short_zycaOUzrvFM|https://www.youtube.com/shorts/zycaOUzrvFM"
  "greg_short_s6qML2WqqBk|https://www.youtube.com/shorts/s6qML2WqqBk"
  "greg_short_PMXswK3pj6k|https://www.youtube.com/shorts/PMXswK3pj6k"
  "greg_short_V3nBwMiSM8Y|https://www.youtube.com/shorts/V3nBwMiSM8Y"
  "greg_short_4kkJ_HRCbrM|https://www.youtube.com/shorts/4kkJ_HRCbrM"
  "greg_short_6mM5AuGXzPk|https://www.youtube.com/shorts/6mM5AuGXzPk"
  "greg_short_SwSaubfGoiY|https://www.youtube.com/shorts/SwSaubfGoiY"
  "greg_short_JWqIR2w1t4U|https://www.youtube.com/shorts/JWqIR2w1t4U"
  "greg_short_68k60GHZDT8|https://www.youtube.com/shorts/68k60GHZDT8"
  "greg_short_jeIAslyP8HE|https://www.youtube.com/shorts/jeIAslyP8HE"
  "greg_short_Dcvuvik1FNg|https://www.youtube.com/shorts/Dcvuvik1FNg"
  "greg_short_MAtPxn5G_3E|https://www.youtube.com/shorts/MAtPxn5G_3E"
  "greg_short_VxYbhY9cS18|https://www.youtube.com/shorts/VxYbhY9cS18"
  "greg_short_6ExK9BNS2ok|https://www.youtube.com/shorts/6ExK9BNS2ok"
  "greg_short_UGf9BPXlSm4|https://www.youtube.com/shorts/UGf9BPXlSm4"
  "greg_short_OgZcHGxLEH4|https://www.youtube.com/shorts/OgZcHGxLEH4"
  "greg_short_1FpgWxCUtxk|https://www.youtube.com/shorts/1FpgWxCUtxk"
  "greg_short__dJum_wHkJk|https://www.youtube.com/shorts/_dJum_wHkJk"
  "greg_short_06iMR4RT_L4|https://www.youtube.com/shorts/06iMR4RT_L4"
  "greg_short_wd2cHEzxO0M|https://www.youtube.com/shorts/wd2cHEzxO0M"
  "greg_short_VvnBlPGKMYM|https://www.youtube.com/shorts/VvnBlPGKMYM"
  "greg_short_Zp_-WIhPBtA|https://www.youtube.com/shorts/Zp_-WIhPBtA"
  "greg_short_h0T5XE-5cv4|https://www.youtube.com/shorts/h0T5XE-5cv4"
  "greg_short_OP3eDXegd34|https://www.youtube.com/shorts/OP3eDXegd34"
  "greg_short_aeWQn-NBMSY|https://www.youtube.com/shorts/aeWQn-NBMSY"
  "greg_short__PhUg8fDV8Q|https://www.youtube.com/shorts/_PhUg8fDV8Q"
  "greg_short_0SpwUkf_D2Q|https://www.youtube.com/shorts/0SpwUkf_D2Q"
  "greg_short_6mHcXZ5X0Q0|https://www.youtube.com/shorts/6mHcXZ5X0Q0"
  "greg_short_sC5mDcbnf74|https://www.youtube.com/shorts/sC5mDcbnf74"
  "greg_short_-PykBdyU4_o|https://www.youtube.com/shorts/-PykBdyU4_o"
  "greg_short_zXNnhsTwK40|https://www.youtube.com/shorts/zXNnhsTwK40"
  "greg_short_ymtUi8hd3Ao|https://www.youtube.com/shorts/ymtUi8hd3Ao"
  "greg_short_q8JnecNosbk|https://www.youtube.com/shorts/q8JnecNosbk"
  "greg_short_bkHbK1vaHMU|https://www.youtube.com/shorts/bkHbK1vaHMU"
  "greg_short_pLcJGOfLFoI|https://www.youtube.com/shorts/pLcJGOfLFoI"
  "greg_short_egvbj1NMVME|https://www.youtube.com/shorts/egvbj1NMVME"
  "greg_short_4xCecXgUeXk|https://www.youtube.com/shorts/4xCecXgUeXk"
  "greg_short_OveNJiMmMHY|https://www.youtube.com/shorts/OveNJiMmMHY"
  "greg_short_22ykPFyh2u4|https://www.youtube.com/shorts/22ykPFyh2u4"
  "greg_short_QXie_Isr6a8|https://www.youtube.com/shorts/QXie_Isr6a8"
  "greg_short_8tzfYnFbk18|https://www.youtube.com/shorts/8tzfYnFbk18"
  "greg_short_25LioIEukQ4|https://www.youtube.com/shorts/25LioIEukQ4"
  "greg_short_Zg7sEd6LKLU|https://www.youtube.com/shorts/Zg7sEd6LKLU"
  "greg_short_N9l-snZ0VSE|https://www.youtube.com/shorts/N9l-snZ0VSE"
  "greg_short_R5LIXUQjxfs|https://www.youtube.com/shorts/R5LIXUQjxfs"
  "greg_short_2p1uwLZefqg|https://www.youtube.com/shorts/2p1uwLZefqg"
  "greg_short_FhH8CRUnwZw|https://www.youtube.com/shorts/FhH8CRUnwZw"
  "greg_short_rJNlHiPHshs|https://www.youtube.com/shorts/rJNlHiPHshs"
  "greg_short_CkSKz9ciPuQ|https://www.youtube.com/shorts/CkSKz9ciPuQ"
  "greg_short_nTjvJN6J5iA|https://www.youtube.com/shorts/nTjvJN6J5iA"
  "greg_short_e3Y-NypkvBQ|https://www.youtube.com/shorts/e3Y-NypkvBQ"
  "greg_short_HN8THBqQxgg|https://www.youtube.com/shorts/HN8THBqQxgg"
  "greg_short_17ZkNrQeuNo|https://www.youtube.com/shorts/17ZkNrQeuNo"
  "greg_short_IzPkhMMOGT8|https://www.youtube.com/shorts/IzPkhMMOGT8"
  "greg_short_s3T2GqlFikQ|https://www.youtube.com/shorts/s3T2GqlFikQ"
  "greg_short_wWzqYsnIx9k|https://www.youtube.com/shorts/wWzqYsnIx9k"
  "greg_short_eyVGlztA_HU|https://www.youtube.com/shorts/eyVGlztA_HU"
  "greg_short_G7tGDpbRa7Q|https://www.youtube.com/shorts/G7tGDpbRa7Q"
  "greg_short_9DKxQnaVnxw|https://www.youtube.com/shorts/9DKxQnaVnxw"
  "greg_short_FqIUcq2Lo7E|https://www.youtube.com/shorts/FqIUcq2Lo7E"
  "greg_short_9aSJk5VK0gM|https://www.youtube.com/shorts/9aSJk5VK0gM"
  "greg_short_uW4HEvSTsbk|https://www.youtube.com/shorts/uW4HEvSTsbk"
  "greg_short_4nMX2kQrRec|https://www.youtube.com/shorts/4nMX2kQrRec"
  "greg_short_QPJ57GeJctg|https://www.youtube.com/shorts/QPJ57GeJctg"
  "greg_short_6zsun4wf5zQ|https://www.youtube.com/shorts/6zsun4wf5zQ"
  "greg_short_PoNyyG7XbeE|https://www.youtube.com/shorts/PoNyyG7XbeE"
  "greg_short_UIu3Y9Qnfig|https://www.youtube.com/shorts/UIu3Y9Qnfig"
  "greg_short_4NSdJrCgW70|https://www.youtube.com/shorts/4NSdJrCgW70"
  "greg_short_yBjZ3Hsiryk|https://www.youtube.com/shorts/yBjZ3Hsiryk"
  "greg_short_7ukMN5E0yIs|https://www.youtube.com/shorts/7ukMN5E0yIs"
  "greg_short_KHJJYjSzf8g|https://www.youtube.com/shorts/KHJJYjSzf8g"
  "greg_short_imghCtCRqWI|https://www.youtube.com/shorts/imghCtCRqWI"
  "greg_short_Fg-Hv-5HzXA|https://www.youtube.com/shorts/Fg-Hv-5HzXA"
  "greg_short_oqX3wWAWW3Y|https://www.youtube.com/shorts/oqX3wWAWW3Y"
  "greg_short_CVFRIVL0sj8|https://www.youtube.com/shorts/CVFRIVL0sj8"
  "greg_short_5IWRV_J6OGQ|https://www.youtube.com/shorts/5IWRV_J6OGQ"
  "greg_short_jcSu8B4aiQc|https://www.youtube.com/shorts/jcSu8B4aiQc"
  "greg_short_NXbFIfZn0QE|https://www.youtube.com/shorts/NXbFIfZn0QE"
  "greg_short_HvogIpx3SsY|https://www.youtube.com/shorts/HvogIpx3SsY"
  "greg_short_pEHeHdM6FG4|https://www.youtube.com/shorts/pEHeHdM6FG4"
  "greg_short_CcRoqbUK5D0|https://www.youtube.com/shorts/CcRoqbUK5D0"
  "greg_short_RggquHho7UM|https://www.youtube.com/shorts/RggquHho7UM"
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

    # Patch .meta.json with content_category + creator (see §5)
    meta="transcripts/${slug}.meta.json"
    if [[ -f "$meta" ]]; then
      python3 -c "
import json
p = '$meta'
d = json.load(open(p))
d['content_category'] = 'dtc_brand_building'
d['creator'] = 'Greg LaVecchia'
d['channel_handle'] = '@GregLaVecchia'
d['brand'] = 'Bloom Nutrition'
json.dump(d, open(p,'w'), indent=2)
"
    fi
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

Unattended. ~1-2 h. Each transcript + `.meta.json` uploads to GitHub as its own commit directly to `origin/main` (no PR).

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

The ingest loop patches the sidecar inline after each `OK` (see §4). This differentiates the corpus from the Ericvelch batch (`service_business_lead_gen`) at Atlas retrieval time.

---

## 6. Verify + notify

```bash
tail -10 ingest_greg.log
gh api "repos/pranavgupta55/Scribe/contents/transcripts" --paginate --jq '[.[] | select(.name | startswith("greg_short_") or startswith("bloom_") or startswith("1b_") or startswith("500m_") or startswith("168_") or startswith("built_") or startswith("before_") or startswith("ten_yrs_") or startswith("unlocking_") or startswith("best_friend_") or startswith("undisciplined_") or startswith("how_millionaires_") or startswith("launch_product_") or startswith("raw_founder_") or startswith("two_gen_") or startswith("promised_myself_") or startswith("million_dollar_"))] | length'
```

If `ok >= 110` (of 120) print `M4 GREG INGEST COMPLETE` and stop. If `ok < 110`, print the failing slugs and stop.

**Do NOT run `updateDB.sh`.** Knowledge extraction runs separately after this batch is confirmed complete.

---

## 7. What NOT to do

- Do **not** modify `scribe.sh`, `process.py`, or `qwen_transcribe.py`.
- Do **not** run `updateDB.sh`.
- Do **not** commit anything outside `transcripts/`.
- Do **not** delete `~/Desktop/scribe-m4-greg/` until the human confirms.
- Do **not** hunt for videos beyond the 120 in the array above — this list is authoritative (verified via `yt-dlp --flat-playlist` against the live channel).
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
