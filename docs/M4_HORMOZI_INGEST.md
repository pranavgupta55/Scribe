# M4 batch ingest: Alex Hormozi additions → Scribe

> **Purpose:** M4 transcribes the top 270 newest-yet-unprocessed videos from @AlexHormozi (dedup'd against every existing Hormozi transcript already in Scribe). Fills the gap between the earlier ~295-video Hormozi corpus and today (2026-07-04).
>
> **You (the M4 Claude agent) are on direct API billing. Do not deliberate. Do not restructure. Execute in order. If a step fails, print the error and stop.**

---

## 0. What this run does

- Fresh-clones Scribe into `~/Desktop/scribe-m4-hormozi/` (any existing Scribe install is assumed broken).
- Runs `setup.sh` to pull whisper + qwen3:1.7b + nomic-embed-text (~3.3 GB, one-time; skipped if models already present).
- Transcribes 270 newest unprocessed Hormozi videos: ~150.1h of audio → ~13h wall-clock at M4 whisper throughput (~14× realtime including download + upload overhead).
- Uploads each transcript + `.meta.json` sidecar directly to `github.com/pranavgupta55/Scribe` via GitHub API (atomic per-file push to `origin/main`, no PR).
- **Does NOT run `updateDB.sh`** — knowledge extraction is a separate step run manually after M4 finishes.

**Expected wall-clock: ~12-14 h unattended. Runs comfortably overnight.**

---

## SCOPE NOTE — READ THIS FIRST

- Video list below was generated on 2026-07-04 by dumping `yt-dlp --flat-playlist https://www.youtube.com/@AlexHormozi/videos` and diffing against every `.txt` in `transcripts/` (matching by `_<11-char YouTube ID>.txt` suffix in the existing filenames).
- 507 videos on channel · 320 already had matching IDs · 471 unprocessed candidates · top 270 by upload recency selected.
- Content mix: ~155 long-form (>20 min), ~77 mid (5-20 min), ~8 shorts.
- If YouTube rejects a video (deleted / private / age-gated), the ingest loop marks it FAIL and continues.

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
rm -rf scribe-m4-hormozi
git clone https://github.com/pranavgupta55/Scribe.git scribe-m4-hormozi
cd scribe-m4-hormozi
bash setup.sh
```

`setup.sh` pulls ollama models (~2 min if fresh; instant if already present). If it errors, print the last 30 lines and stop.

---

## 3. Ingest script

Save as `ingest_hormozi.sh` in `~/Desktop/scribe-m4-hormozi/`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Each transcript pushes to GitHub as its own commit (atomic per-file).
unset SCRIBE_NO_UPLOAD

# slug|url — 270 videos, newest first (from yt-dlp on 2026-07-04)
VIDEOS=(

  "8_entrepreneurs_compete_for_100000_-_episode_1_zzleYxkf39k|https://www.youtube.com/watch?v=zzleYxkf39k"
  "i_cant_hide_this_anymore_Z5rKn1ZIp3A|https://www.youtube.com/watch?v=Z5rKn1ZIp3A"
  "if_you_want_2026_to_be_the_best_year_of_your_life_please_wat_vhOV_Od0A3M|https://www.youtube.com/watch?v=vhOV_Od0A3M"
  "if_youre_ambitious_but_inconsistent_please_watch_this_UDBkiBnMrHs|https://www.youtube.com/watch?v=UDBkiBnMrHs"
  "how_to_speak_so_well_people_give_you_money_sGakuNs9mT4|https://www.youtube.com/watch?v=sGakuNs9mT4"
  "watch_this_to_generate_1000s_of_leads_in_any_niche_Mst4hreQYl0|https://www.youtube.com/watch?v=Mst4hreQYl0"
  "if_i_wanted_to_create_a_business_that_runs_itself_heres_what_mr4Pw66_490|https://www.youtube.com/watch?v=mr4Pw66_490"
  "if_youre_in_your_20s_or_30s_heres_how_to_win_at_anything_0lMn_-EXyhQ|https://www.youtube.com/watch?v=0lMn_-EXyhQ"
  "you_can_make_way_more_money_than_you_think_hnmBXTyMFKI|https://www.youtube.com/watch?v=hnmBXTyMFKI"
  "how_to_force_yourself_to_be_consistent_and_do_hard_things_WOZGZK7K-Jo|https://www.youtube.com/watch?v=WOZGZK7K-Jo"
  "my_actual_social_media_strategy_for_2026_dMZ-n2KSlxE|https://www.youtube.com/watch?v=dMZ-n2KSlxE"
  "building_a_1000000_business_for_a_stranger_in_69_minutes_Wr6n_zNKvMk|https://www.youtube.com/watch?v=Wr6n_zNKvMk"
  "if_you_have_a_hard_life_watch_this_Avp3xh3Y1Ic|https://www.youtube.com/watch?v=Avp3xh3Y1Ic"
  "give_me_20_minutes_and_ill_give_you_back_20_years_of_your_li_3SVksBB3_YY|https://www.youtube.com/watch?v=3SVksBB3_YY"
  "if_youre_watching_this_we_did_it_q_4P7FOFEEI|https://www.youtube.com/watch?v=q_4P7FOFEEI"
  "my_final_update_tGhe3sBuk34|https://www.youtube.com/watch?v=tGhe3sBuk34"
  "100m_money_models_rick_roll_c6wfunUDDOs|https://www.youtube.com/watch?v=c6wfunUDDOs"
  "your_biggest_advantage_is_no_one_knows_who_you_are_prj1bnTAM8A|https://www.youtube.com/watch?v=prj1bnTAM8A"
  "i_blew_up_a_business_through_3_small_changes_just_copy_me_3yAiVjcImQ4|https://www.youtube.com/watch?v=3yAiVjcImQ4"
  "youre_setting_goals_wrong_XwzU4RikbGs|https://www.youtube.com/watch?v=XwzU4RikbGs"
  "making_money_is_a_game_heres_the_cheat_code_nSQdjim8CsE|https://www.youtube.com/watch?v=nSQdjim8CsE"
  "copy_this_strategy_itll_blow_up_your_business_DaRXece2ItE|https://www.youtube.com/watch?v=DaRXece2ItE"
  "my_biggest_announcement_ever_zgYQT-fdLL4|https://www.youtube.com/watch?v=zgYQT-fdLL4"
  "if_youre_ambitious_and_in_your_20s_or_30s_please_watch_this_ub1D6RQocRU|https://www.youtube.com/watch?v=ub1D6RQocRU"
  "dangerously_honest_advice_to_create_generational_wealth_m-k0_pQJ1fY|https://www.youtube.com/watch?v=m-k0_pQJ1fY"
  "i_blew_up_a_secret_business_to_prove_its_not_luck_SmiOK8Yun4s|https://www.youtube.com/watch?v=SmiOK8Yun4s"
  "youll_find_this_video_when_you_need_it_most_IFElGv5ZmRM|https://www.youtube.com/watch?v=IFElGv5ZmRM"
  "we_made_a_big_decision_OUI12JmD-lM|https://www.youtube.com/watch?v=OUI12JmD-lM"
  "how_i_would_build_a_business_in_2026_if_i_had_to_start_over_i7bLRKwKSms|https://www.youtube.com/watch?v=i7bLRKwKSms"
  "why_doing_more_is_actually_making_you_less_money_hfwZwPGsbIo|https://www.youtube.com/watch?v=hfwZwPGsbIo"
  "give_me_14_minutes_and_youll_learn_how_to_be_absurdly_happy_UulLbNJMpTg|https://www.youtube.com/watch?v=UulLbNJMpTg"
  "how_to_grow_your_business_so_fast_it_makes_your_accountant_n_sGv2BTUCcCM|https://www.youtube.com/watch?v=sGv2BTUCcCM"
  "building_a_2300000yr_business_for_a_stranger_in_57_mins_-Koq14DPXC4|https://www.youtube.com/watch?v=-Koq14DPXC4"
  "how_to_start_a_business_from_nothing_thank_me_later_unshZobTt6Q|https://www.youtube.com/watch?v=unshZobTt6Q"
  "less_is_more_the_magic_of_a_simple_business_-JLN1d1ZKUU|https://www.youtube.com/watch?v=-JLN1d1ZKUU"
  "business_is_now_on_easy_mode_in_2026_heres_why_Ds_Qp2U5I8U|https://www.youtube.com/watch?v=Ds_Qp2U5I8U"
  "how_to_progress_way_faster_than_anyone_else_-UzJOk85OZI|https://www.youtube.com/watch?v=-UzJOk85OZI"
  "stop_caring_what_others_think_of_you_so_much_qqjGxVW-Ae0|https://www.youtube.com/watch?v=qqjGxVW-Ae0"
  "why_being_fast_makes_you_always_win_thank_me_later_fcyIoN8CUOk|https://www.youtube.com/watch?v=fcyIoN8CUOk"
  "how_to_get_anyone_to_do_anything_you_want_aUnorufFIq8|https://www.youtube.com/watch?v=aUnorufFIq8"
  "14_years_of_marketing_advice_in_35_minutes_JDR-R--4HhM|https://www.youtube.com/watch?v=JDR-R--4HhM"
  "build_your_business_like_harvard_FiL0r5_dUvM|https://www.youtube.com/watch?v=FiL0r5_dUvM"
  "if_you_want_to_win_in_life_please_watch_this_IYuiV4YuGB4|https://www.youtube.com/watch?v=IYuiV4YuGB4"
  "business_is_hard_until_you_do_this_CoPs-Bk8M9Y|https://www.youtube.com/watch?v=CoPs-Bk8M9Y"
  "building_a_3500000_business_for_two_strangers_in_52_minutes_h9UyQiLjSHg|https://www.youtube.com/watch?v=h9UyQiLjSHg"
  "how_to_buy_anything_you_want_without_going_broke_by3ZEoo-Quc|https://www.youtube.com/watch?v=by3ZEoo-Quc"
  "how_to_get_what_you_want_every_time_Jc2UW3nlNBA|https://www.youtube.com/watch?v=Jc2UW3nlNBA"
  "the_biggest_myth_you_probably_still_believe_kQFSiEDvXws|https://www.youtube.com/watch?v=kQFSiEDvXws"
  "i_just_learned_this_after_14_years_of_business_uYds0zcAFWM|https://www.youtube.com/watch?v=uYds0zcAFWM"
  "4_ways_to_get_ahead_of_99_of_people_s7QA1TJKlbQ|https://www.youtube.com/watch?v=s7QA1TJKlbQ"
  "watch_these_51_minutes_if_you_want_to_grow_your_business_in_-HJg4TYBgtI|https://www.youtube.com/watch?v=-HJg4TYBgtI"
  "the_top_1_of_business_owners_arent_smarter_than_you_q9OmYf_IlQ0|https://www.youtube.com/watch?v=q9OmYf_IlQ0"
  "i_confronted_a_stranger_with_a_failing_business_6Fg4VXjRphQ|https://www.youtube.com/watch?v=6Fg4VXjRphQ"
  "why_following_your_dreams_is_keeping_you_poor_6uhd-FiCggg|https://www.youtube.com/watch?v=6uhd-FiCggg"
  "brutally_honest_truths_that_give_you_an_unfair_advantage_in_MzAIP_WJ-jE|https://www.youtube.com/watch?v=MzAIP_WJ-jE"
  "building_a_3000000_business_for_a_stranger_in_57_mins_n6SHLmKcY0E|https://www.youtube.com/watch?v=n6SHLmKcY0E"
  "he_makes_600000000yr_selling_bacon_orMbq2LtzKE|https://www.youtube.com/watch?v=orMbq2LtzKE"
  "100m_investor_answers_the_most_important_business_questions_--9kOXNCUdE|https://www.youtube.com/watch?v=--9kOXNCUdE"
  "building_a_3500000_business_for_a_stranger_in_51_mins_BMF2fWHyDrg|https://www.youtube.com/watch?v=BMF2fWHyDrg"
  "steal_ryan_reynolds_13b_marketing_strategy_no_fame_required_HwPXctiw_rY|https://www.youtube.com/watch?v=HwPXctiw_rY"
  "answering_your_top_business_questions_for_1_hour_QwEb78e5a8Y|https://www.youtube.com/watch?v=QwEb78e5a8Y"
  "i_ranked_the_best_superbowl_ads_2026_tier_list_npi7UeOE_0o|https://www.youtube.com/watch?v=npi7UeOE_0o"
  "building_a_5000000_business_for_a_stranger_in_42_mins_sBJppqCeFGI|https://www.youtube.com/watch?v=sBJppqCeFGI"
  "breaking_down_rihannas_company_real_tactics_you_can_use_eX3Ch_HuR70|https://www.youtube.com/watch?v=eX3Ch_HuR70"
  "how_to_become_ultra_wealthy_4_methods_BO_59sGxztY|https://www.youtube.com/watch?v=BO_59sGxztY"
  "building_a_1000000_business_for_a_stranger_in_56_mins_nrounb8NlFQ|https://www.youtube.com/watch?v=nrounb8NlFQ"
  "this_video_should_be_required_viewing_for_business_owners_4twK8Yl4iUI|https://www.youtube.com/watch?v=4twK8Yl4iUI"
  "give_me_47_minutes_and_ill_fix_your_small_business_sjt5G3YPjmY|https://www.youtube.com/watch?v=sjt5G3YPjmY"
  "the_ultimate_sales_training_for_2026_full_course_StVqS0jD7Ls|https://www.youtube.com/watch?v=StVqS0jD7Ls"
  "how_to_grow_your_business_so_fast_in_2026_it_feels_illegal_HxEQCHpZzHk|https://www.youtube.com/watch?v=HxEQCHpZzHk"
  "if_you_want_2026_to_be_the_best_year_of_your_life_please_wat_6KqndZuN_Yk|https://www.youtube.com/watch?v=6KqndZuN_Yk"
  "this_video_will_make_your_business_unstoppable_thDTmy7VGIw|https://www.youtube.com/watch?v=thDTmy7VGIw"
  "no_bs_business_advice_to_get_rich_in_2026_cnbHgYYHzyE|https://www.youtube.com/watch?v=cnbHgYYHzyE"
  "how_to_get_customers_so_fast_it_feels_illegal_FMzKk73iUhw|https://www.youtube.com/watch?v=FMzKk73iUhw"
  "if_you_have_less_than_10000_saved_please_watch_this_video_ymFWgFiKUvM|https://www.youtube.com/watch?v=ymFWgFiKUvM"
  "how_to_get_so_rich_you_realize_money_has_no_meaning_4Yz8ggEv0NU|https://www.youtube.com/watch?v=4Yz8ggEv0NU"
  "how_the_top_1_actually_make_their_money_wtsX7WHQMFM|https://www.youtube.com/watch?v=wtsX7WHQMFM"
  "how_to_make_money_so_fast_it_feels_illegal_k-3PoOT4vOM|https://www.youtube.com/watch?v=k-3PoOT4vOM"
  "youre_wasting_your_chance_to_make_insane_money_QTZsh3BgOwY|https://www.youtube.com/watch?v=QTZsh3BgOwY"
  "the_3_fastest_ways_i_know_to_grow_any_business_ovL6Z5z0jxQ|https://www.youtube.com/watch?v=ovL6Z5z0jxQ"
  "revealing_black_friday_secrets_to_use_in_your_business_feat_YUXLJauT4eY|https://www.youtube.com/watch?v=YUXLJauT4eY"
  "no_bs_advice_to_get_rich_like_the_1_oDK4g5na4Jw|https://www.youtube.com/watch?v=oDK4g5na4Jw"
  "why_the_best_business_to_start_in_2026_is_a_skool_community_rn5-yLUaNw0|https://www.youtube.com/watch?v=rn5-yLUaNw0"
  "my_full_workout_with_chris_bumstead_6x_mr_olympia_champion_7qy-EPc2gYU|https://www.youtube.com/watch?v=7qy-EPc2gYU"
  "its_actually_pretty_easy_to_get_ahead_of_99_of_people_TFxT3G5jwtU|https://www.youtube.com/watch?v=TFxT3G5jwtU"
  "learn_email_marketing_in_39_minutes_pLhQOYMGa88|https://www.youtube.com/watch?v=pLhQOYMGa88"
  "this_business_was_stuck_heres_how_i_fixed_it_4GQLJjH9-oA|https://www.youtube.com/watch?v=4GQLJjH9-oA"
  "if_i_had_to_start_a_business_from_scratch_id_do_this_HsQeQM1jUeg|https://www.youtube.com/watch?v=HsQeQM1jUeg"
  "if_i_wanted_to_become_a_millionaire_in_2026_this_is_what_id_AN2KpRBsmRY|https://www.youtube.com/watch?v=AN2KpRBsmRY"
  "after_closing_4000_sales_i_discovered_a_new_method_to_close_RVbvhPGFi6E|https://www.youtube.com/watch?v=RVbvhPGFi6E"
  "my_evidence_based_guide_to_making_money_online_tier_list_wR8KoE8u1p0|https://www.youtube.com/watch?v=wR8KoE8u1p0"
  "the_real_reason_youre_not_making_as_much_money_as_you_want_zNiXk_3C_Io|https://www.youtube.com/watch?v=zNiXk_3C_Io"
  "3_hours_of_money_making_advice_you_needed_to_know_yesterday_TooAB8Ow6cQ|https://www.youtube.com/watch?v=TooAB8Ow6cQ"
  "how_to_articulate_your_thoughts_more_clearly_than_99_of_peop_s6tkRztZwYc|https://www.youtube.com/watch?v=s6tkRztZwYc"
  "no_bs_advice_to_get_rich_in_the_next_10_years_oZ-H_TjSzok|https://www.youtube.com/watch?v=oZ-H_TjSzok"
  "watch_this_to_keep_more_customers_afbP6sB_Atc|https://www.youtube.com/watch?v=afbP6sB_Atc"
  "more_followers_wont_make_you_rich_but_this_will_lEIqyLE4iOY|https://www.youtube.com/watch?v=lEIqyLE4iOY"
  "13_years_of_no_bs_business_advice_in_79_mins_oRMG_HpOAN4|https://www.youtube.com/watch?v=oRMG_HpOAN4"
  "how_the_top_1_make_their_money_fD-sxKiB30M|https://www.youtube.com/watch?v=fD-sxKiB30M"
  "how_to_sell_better_than_99_of_people_4_hour_ultimate_guide_JE2_7elAcxM|https://www.youtube.com/watch?v=JE2_7elAcxM"
  "4_steps_to_unf_your_business_kloJJeiysxg|https://www.youtube.com/watch?v=kloJJeiysxg"
  "building_a_brand_but_its_on_easy_mode_instead_UGEc9-7X3OQ|https://www.youtube.com/watch?v=UGEc9-7X3OQ"
  "how_to_actually_get_rich_in_your_20s_aFoMYz_jWcs|https://www.youtube.com/watch?v=aFoMYz_jWcs"
  "10_millionaires_asked_me_how_to_get_richer_6m6DCQMASEM|https://www.youtube.com/watch?v=6m6DCQMASEM"
  "learn_paid_ads_in_30_minutes_fSbqaTlWaYI|https://www.youtube.com/watch?v=fSbqaTlWaYI"
  "genius_strategy_to_make_everyone_want_to_buy_your_stuff_5MjjpB8SPMo|https://www.youtube.com/watch?v=5MjjpB8SPMo"
  "business_was_hard_until_i_understood_these_4_concepts_F84olnKkseM|https://www.youtube.com/watch?v=F84olnKkseM"
  "13_ways_to_destroy_your_competition_legally_Lc8DNduiwKA|https://www.youtube.com/watch?v=Lc8DNduiwKA"
  "58_mins_of_advice_that_will_blow_up_your_business_rc7cxL7ql7Y|https://www.youtube.com/watch?v=rc7cxL7ql7Y"
  "business_owners_we_have_a_problem_rhVxX5_8xUw|https://www.youtube.com/watch?v=rhVxX5_8xUw"
  "sick_of_shiny_object_syndrome_watch_this_07jC6ooRIHw|https://www.youtube.com/watch?v=07jC6ooRIHw"
  "i_discovered_the_easiest_million_dollar_business_to_start_in_Rm4zRdLAyjw|https://www.youtube.com/watch?v=Rm4zRdLAyjw"
  "youre_not_behind_my_system_for_outworking_everyone_gD0X-PLax5I|https://www.youtube.com/watch?v=gD0X-PLax5I"
  "business_owners_you_need_to_know_this_number_jzKpAtzKQ54|https://www.youtube.com/watch?v=jzKpAtzKQ54"
  "im_broke_what_business_do_i_start_nIk3DedjxJM|https://www.youtube.com/watch?v=nIk3DedjxJM"
  "you_get_rich_by_not_focusing_on_getting_rich_Uki3IUkUu7Q|https://www.youtube.com/watch?v=Uki3IUkUu7Q"
  "sales_was_hard_until_i_understood_these_9_concepts_cy2k1GdA-9o|https://www.youtube.com/watch?v=cy2k1GdA-9o"
  "your_failures_can_make_you_rich_if_you_write_them_down_SasEJE4FI-I|https://www.youtube.com/watch?v=SasEJE4FI-I"
  "7_ways_to_get_customers_for_free_qpQvdBFW_yI|https://www.youtube.com/watch?v=qpQvdBFW_yI"
  "7_millionaires_asked_me_how_to_get_richer_bgBIO6nZawg|https://www.youtube.com/watch?v=bgBIO6nZawg"
  "youre_wasting_80_of_your_time_heres_how_to_fix_it_GIRkQQHzsxI|https://www.youtube.com/watch?v=GIRkQQHzsxI"
  "this_email_campaign_generates_sales_full_breakdown_OpeN4O5myIg|https://www.youtube.com/watch?v=OpeN4O5myIg"
  "stop_being_broke_get_busy_H_ZLs1-jOKQ|https://www.youtube.com/watch?v=H_ZLs1-jOKQ"
  "make_more_profit_than_99_of_people_41EvCgwPrDc|https://www.youtube.com/watch?v=41EvCgwPrDc"
  "how_to_build_a_valuable_company_you_can_sell_someday_VxKwz6hBVZU|https://www.youtube.com/watch?v=VxKwz6hBVZU"
  "10x_your_profit_10x_your_pricing_10x_your_business_heres_how_9unucIBuNio|https://www.youtube.com/watch?v=9unucIBuNio"
  "you_made_your_customers_angry_now_what_p39nLmVtOjc|https://www.youtube.com/watch?v=p39nLmVtOjc"
  "if_youre_not_unbelievably_rich_yet_this_is_why_h6y0nYVZgwE|https://www.youtube.com/watch?v=h6y0nYVZgwE"
  "13_years_of_marketing_advice_in_85_mins_reisEL_D7xc|https://www.youtube.com/watch?v=reisEL_D7xc"
  "she_turned_5800_followers_into_1_million_per_year_0S5xsICW8qg|https://www.youtube.com/watch?v=0S5xsICW8qg"
  "i_posted_only_business_content_for_30_days_strategy_revealed_JOfsujnXxCg|https://www.youtube.com/watch?v=JOfsujnXxCg"
  "your_business_is_not_what_you_think_it_is_rj7rzOdj84A|https://www.youtube.com/watch?v=rj7rzOdj84A"
  "this_idea_will_make_your_business_unstoppable_m5ordaa7NN4|https://www.youtube.com/watch?v=m5ordaa7NN4"
  "youre_not_behind_my_system_for_outlearning_everyone_3Ju1I37jWUM|https://www.youtube.com/watch?v=3Ju1I37jWUM"
  "business_owners_youre_probably_not_good_enough_Luvfw14pVx4|https://www.youtube.com/watch?v=Luvfw14pVx4"
  "10x_revenue_with_1_new_sales_process_you_can_steal_it_1UhvBSQFy6A|https://www.youtube.com/watch?v=1UhvBSQFy6A"
  "13_years_of_brutally_honest_business_advice_in_90_mins_KhFlD54nQrY|https://www.youtube.com/watch?v=KhFlD54nQrY"
  "the_ultra_rich_playbook_legal_tax-free_6x3re5-Ms1E|https://www.youtube.com/watch?v=6x3re5-Ms1E"
  "if_i_wanted_to_monetize_an_audience_in_2026_this_is_what_i_w_oRqnTOT9ZG8|https://www.youtube.com/watch?v=oRqnTOT9ZG8"
  "how_to_10x_your_business_overnight_with_influencers_Z2tgZC_XkT4|https://www.youtube.com/watch?v=Z2tgZC_XkT4"
  "how_i_gained_78_million_followers_in_40_months_6_key_lessons_HVOubeXUcx0|https://www.youtube.com/watch?v=HVOubeXUcx0"
  "7_ways_to_get_people_to_buy_more_times_sPkMHh8zTMI|https://www.youtube.com/watch?v=sPkMHh8zTMI"
  "100m_ceo_explains_how_to_build_a_brand_in_2024_VQM3DrnVTcs|https://www.youtube.com/watch?v=VQM3DrnVTcs"
  "541_of_people_who_try_this_make_their_1st_dollar_online_IFpHasZ7jN4|https://www.youtube.com/watch?v=IFpHasZ7jN4"
  "advice_i_wish_someone_gave_me_when_i_was_broke_oys_fv25SYM|https://www.youtube.com/watch?v=oys_fv25SYM"
  "i_lost_100_million_heres_what_i_learned_tdLLGKoBojo|https://www.youtube.com/watch?v=tdLLGKoBojo"
  "watch_this_if_you_want_to_find_your_purpose_in_life_M4evdxF5G0s|https://www.youtube.com/watch?v=M4evdxF5G0s"
  "the_alex_hormozi_guide_to_haters_revealed_UxQJ64BNuco|https://www.youtube.com/watch?v=UxQJ64BNuco"
  "money_lessons_i_learned_in_marriage_oK2_u-uS_Bo|https://www.youtube.com/watch?v=oK2_u-uS_Bo"
  "seriously_watch_this_before_you_go_to_college_nxL4ZfVqKLI|https://www.youtube.com/watch?v=nxL4ZfVqKLI"
  "if_i_wanted_to_get_rich_and_famous_this_is_what_id_do_5JLqmQlGG0U|https://www.youtube.com/watch?v=5JLqmQlGG0U"
  "if_i_were_single_and_starting_over_this_is_what_i_would_do_jiCGLDhUCHY|https://www.youtube.com/watch?v=jiCGLDhUCHY"
  "if_you_want_to_start_a_business_in_2024_watch_this_interview_YZdE8U5eD_s|https://www.youtube.com/watch?v=YZdE8U5eD_s"
  "brutally_honest_advice_to_build_your_confidence_pt50QF6al8g|https://www.youtube.com/watch?v=pt50QF6al8g"
  "getting_rich_isnt_fun_ep_014_3aAbKVSFP6k|https://www.youtube.com/watch?v=3aAbKVSFP6k"
  "i_helped_business_owners_overcome_their_fear_of_failure_0_Gf5v8DEMY|https://www.youtube.com/watch?v=0_Gf5v8DEMY"
  "brutally_honest_advice_to_my_poorer_younger_self_DiQ3N8F1Hl8|https://www.youtube.com/watch?v=DiQ3N8F1Hl8"
  "the_alex_hormozi_cookbook_revealed_hGX_z5rXRlU|https://www.youtube.com/watch?v=hGX_z5rXRlU"
  "answering_juicy_questions_about_our_relationship_hCgr7jT7JLA|https://www.youtube.com/watch?v=hCgr7jT7JLA"
  "million_dollar_equations_QGcjweehrvU|https://www.youtube.com/watch?v=QGcjweehrvU"
  "charlie_munger_changed_my_life_8_lessons_-NLqtk4F4oY|https://www.youtube.com/watch?v=-NLqtk4F4oY"
  "how_to_win_at_anything_MNll1BaskLA|https://www.youtube.com/watch?v=MNll1BaskLA"
  "im_facing_a_billion_dollar_decision_ep_012_Tu6YDG0AZ5k|https://www.youtube.com/watch?v=Tu6YDG0AZ5k"
  "why_i_dont_go_home_for_the_holidays_OvEfWrDOfNk|https://www.youtube.com/watch?v=OvEfWrDOfNk"
  "the_business_model_thats_so_simple_anyone_can_try_it_CaiLcj6tzBQ|https://www.youtube.com/watch?v=CaiLcj6tzBQ"
  "81_minutes_of_money_making_advice_you_needed_to_know_yesterd_U_s0ekwPK5g|https://www.youtube.com/watch?v=U_s0ekwPK5g"
  "my_full_body_workout_with_chris_williamson_ABOd589OyTM|https://www.youtube.com/watch?v=ABOd589OyTM"
  "if_i_wanted_to_become_a_millionaire_in_2024_this_is_what_id_VBoRLJimVzc|https://www.youtube.com/watch?v=VBoRLJimVzc"
  "how_to_get_rich_this_black_friday_mHrAjWni65E|https://www.youtube.com/watch?v=mHrAjWni65E"
  "how_to_make_so_much_money_it_makes_you_sick_I64gYLBrics|https://www.youtube.com/watch?v=I64gYLBrics"
  "brutally_honest_advice_to_my_younger_poorer_self_ln24y0FPJHo|https://www.youtube.com/watch?v=ln24y0FPJHo"
  "i_tried_this_simple_business_strategy_for_60_days_this_is_wh_yPDQCfrwh8E|https://www.youtube.com/watch?v=yPDQCfrwh8E"
  "i_doubled_a_business_in_60_days_to_show_its_not_luck_part_1_c_6BrF7jOGk|https://www.youtube.com/watch?v=c_6BrF7jOGk"
  "what_now_ep_009_Ktwv_uEW-uA|https://www.youtube.com/watch?v=Ktwv_uEW-uA"
  "this_video_will_make_you_more_money_than_anything_else_on_th_4GjwtnA76ig|https://www.youtube.com/watch?v=4GjwtnA76ig"
  "how_to_get_rich_full_interview_with_dave_ramsey_jvXOOddDg_s|https://www.youtube.com/watch?v=jvXOOddDg_s"
  "i_helped_6_business_owners_make_more_money_aBPWCdaJJqA|https://www.youtube.com/watch?v=aBPWCdaJJqA"
  "i_reverse_engineered_the_perfect_business_HKbFUWJwEG0|https://www.youtube.com/watch?v=HKbFUWJwEG0"
  "i_gained_5000000_followers_with_this_secret_method_fd-hi3NqMYo|https://www.youtube.com/watch?v=fd-hi3NqMYo"
  "behind_the_scenes_of_the_100m_leads_launch_ep_006_qel9bf653Es|https://www.youtube.com/watch?v=qel9bf653Es"
  "behind_the_scenes_of_the_100m_leads_book_launch_official_tra_cHXYi7MqP5c|https://www.youtube.com/watch?v=cHXYi7MqP5c"
  "how_to_get_so_rich_you_question_the_meaning_of_making_money_RdAKXJlMIZM|https://www.youtube.com/watch?v=RdAKXJlMIZM"
  "watch_this_to_get_your_first_5_customers_w7g08dVTwaE|https://www.youtube.com/watch?v=w7g08dVTwaE"
  "my_100m_leads_affiliate_marketing_guide_60_7PU9JDIw|https://www.youtube.com/watch?v=60_7PU9JDIw"
  "make_money_online_without_destroying_your_reputation_5cOwh-8scu8|https://www.youtube.com/watch?v=5cOwh-8scu8"
  "interviewing_a_billionaire_revealing_my_workout_routine_ep_0_y7O-iTWwTW8|https://www.youtube.com/watch?v=y7O-iTWwTW8"
  "giving_away_free_stuff_will_make_you_rich_7NMH1oAkgLY|https://www.youtube.com/watch?v=7NMH1oAkgLY"
  "why_mrbeast_will_be_worth_100_billion_VPre_XMgKjs|https://www.youtube.com/watch?v=VPre_XMgKjs"
  "huge_announcement_zZyRg4Fzabk|https://www.youtube.com/watch?v=zZyRg4Fzabk"
  "how_to_get_ahead_of_99_of_people_Nh8Oc7ERdIU|https://www.youtube.com/watch?v=Nh8Oc7ERdIU"
  "my_12_hour_work_day_in_15_minutes_ep_003_lJF__n_34ew|https://www.youtube.com/watch?v=lJF__n_34ew"
  "watch_this_if_youre_tired_of_being_broke_YFA8AS5Cu2w|https://www.youtube.com/watch?v=YFA8AS5Cu2w"
  "no_new_friends_my_extreme_views_on_friendship_1taVrxMFjaY|https://www.youtube.com/watch?v=1taVrxMFjaY"
  "the_real_reason_your_business_isnt_growing_9gVdCR7W8o8|https://www.youtube.com/watch?v=9gVdCR7W8o8"
  "day_in_the_life_of_alex_hormozi_ep_002_6ySRKgXBcO0|https://www.youtube.com/watch?v=6ySRKgXBcO0"
  "3_ways_to_do_what_you_love_and_get_wealthy_too_qLM5G7N3l3I|https://www.youtube.com/watch?v=qLM5G7N3l3I"
  "3_simple_fixes_that_grow_any_business_KQuyQpFANpA|https://www.youtube.com/watch?v=KQuyQpFANpA"
  "how_to_build_a_legit_online_course_works_in_2026_oTQPxPFROck|https://www.youtube.com/watch?v=oTQPxPFROck"
  "the_alex_hormozi_diet_revealed_fxyhIXZ6Yog|https://www.youtube.com/watch?v=fxyhIXZ6Yog"
  "how_to_go_all_in_on_your_side_hustle_SYkwtqFoRcM|https://www.youtube.com/watch?v=SYkwtqFoRcM"
  "how_i_set_goals_that_actually_make_money_zBZHWrvjD8Y|https://www.youtube.com/watch?v=zBZHWrvjD8Y"
  "im_broke_what_should_i_sell_LVM89ik-7Kw|https://www.youtube.com/watch?v=LVM89ik-7Kw"
  "the_season_of_no_what_it_takes_to_win_ueJg14gQLuc|https://www.youtube.com/watch?v=ueJg14gQLuc"
  "how_to_grow_any_local_business_my_framework_BHMeYaHEMpc|https://www.youtube.com/watch?v=BHMeYaHEMpc"
  "how_to_get_what_you_want_6_proven_methods__PCCqqv2pig|https://www.youtube.com/watch?v=_PCCqqv2pig"
  "why_you_shouldnt_copy_me_zNJ5JzEJgyo|https://www.youtube.com/watch?v=zNJ5JzEJgyo"
  "hardcore_business_lessons_i_learned_from_a_dealer_vZfatNSouDQ|https://www.youtube.com/watch?v=vZfatNSouDQ"
  "17_life-changing_conversations_i_wish_i_had_earlier_ULGT0Qpglek|https://www.youtube.com/watch?v=ULGT0Qpglek"
  "the_1_reason_young_people_stay_poor_-wnnwCqGeNc|https://www.youtube.com/watch?v=-wnnwCqGeNc"
  "i_built_4_businesses_in_a_row_to_show_its_not_luck_0mqqbuM9sAk|https://www.youtube.com/watch?v=0mqqbuM9sAk"
  "how_the_worlds_richest_man_made_his_money_ONV__y1z7MI|https://www.youtube.com/watch?v=ONV__y1z7MI"
  "i_challenged_my_team_to_replace_themselves_with_ai_z7X95bn2T6A|https://www.youtube.com/watch?v=z7X95bn2T6A"
  "get_rich_with_these_24_investing_rules_INm4U2S7Vu8|https://www.youtube.com/watch?v=INm4U2S7Vu8"
  "42_recession_proof_money_hacks_rp1PzCxj3eU|https://www.youtube.com/watch?v=rp1PzCxj3eU"
  "7_secrets_behind_chick-fil-as_50b_empire_TIH1w-KuATk|https://www.youtube.com/watch?v=TIH1w-KuATk"
  "building_a_frozen_yogurt_store_in_11_minutes_xZ8d9g6BcKM|https://www.youtube.com/watch?v=xZ8d9g6BcKM"
  "watch_this_if_youre_feeling_burnt_out_vthPawWn6ws|https://www.youtube.com/watch?v=vthPawWn6ws"
  "get_rich_in_the_ai_revolution_2023_KYqEK_T_5M4|https://www.youtube.com/watch?v=KYqEK_T_5M4"
  "14_money_mistakes_to_avoid_in_your_20s_q6SdmgIji30|https://www.youtube.com/watch?v=q6SdmgIji30"
  "the_best_student_side_hustle_in_2026_CGSd00h-6zI|https://www.youtube.com/watch?v=CGSd00h-6zI"
  "why_looking_poor_is_actually_smart_Day0yToqeco|https://www.youtube.com/watch?v=Day0yToqeco"
  "a_youtuber_asked_me_how_to_business_heres_my_answer_4pZwlE86A5Y|https://www.youtube.com/watch?v=4pZwlE86A5Y"
  "stop_trying_to_be_happy_NDDFezF7OTA|https://www.youtube.com/watch?v=NDDFezF7OTA"
  "how_to_get_customers_to_pay_forever_K8MFC9t7snY|https://www.youtube.com/watch?v=K8MFC9t7snY"
  "this_one_equation_will_make_you_rich_5MHQr-Z17Hc|https://www.youtube.com/watch?v=5MHQr-Z17Hc"
  "how_they_keep_you_poor_forever_yflKMUffctE|https://www.youtube.com/watch?v=yflKMUffctE"
  "billionaire_recession_advice_stop_investing_in_this_2023_GkL2KDOf2NM|https://www.youtube.com/watch?v=GkL2KDOf2NM"
  "10_different_ways_to_break_down_making_1_million_7sLXhCDRaV8|https://www.youtube.com/watch?v=7sLXhCDRaV8"
  "14_life_lessons_i_wish_i_knew_earlier_cq8GyLrEuAk|https://www.youtube.com/watch?v=cq8GyLrEuAk"
  "why_old_friends_keep_you_poor_vVssypj7nYw|https://www.youtube.com/watch?v=vVssypj7nYw"
  "get_rich_in_the_new_economy_6DCDGSnRDtM|https://www.youtube.com/watch?v=6DCDGSnRDtM"
  "hard_work_wont_make_you_rich_PTgGfV8Tf00|https://www.youtube.com/watch?v=PTgGfV8Tf00"
  "i_was_wrong_about_mentorship_39I8jEqFdYc|https://www.youtube.com/watch?v=39I8jEqFdYc"
  "how_to_build_large_sales_teams_starting_from_0_2lA_A8BGRRs|https://www.youtube.com/watch?v=2lA_A8BGRRs"
  "how_i_wrote_a_1_bestseller_with_0_and_no_publisher_-TOYJHax5x8|https://www.youtube.com/watch?v=-TOYJHax5x8"
  "this_one_thing_will_make_you_a_better_entrepreneur_JsXZzgD_k9k|https://www.youtube.com/watch?v=JsXZzgD_k9k"
  "my_best_sales_tactic_to_make_a_ton_of_money_bx48qPlaGvE|https://www.youtube.com/watch?v=bx48qPlaGvE"
  "how_to_never_feel_stressed_again_7DKXLasU4Kg|https://www.youtube.com/watch?v=7DKXLasU4Kg"
  "how_to_make_a_ton_of_money_in_just_a_few_minutes_yEKu6q0W3gs|https://www.youtube.com/watch?v=yEKu6q0W3gs"
  "a_magic_business_genie_grants_you_3_wishes__ArQlwPvGUA|https://www.youtube.com/watch?v=_ArQlwPvGUA"
  "investor_3min_morning_routine_it_just_works_PFKGHL1MqkU|https://www.youtube.com/watch?v=PFKGHL1MqkU"
  "how_to_grow_an_audience_if_you_have_0_followers_7ITff1fIbSc|https://www.youtube.com/watch?v=7ITff1fIbSc"
  "why_you_should_quit_college_5RiR6cBLkFg|https://www.youtube.com/watch?v=5RiR6cBLkFg"
  "from_rock_bottom_to_breakthrough_if_youre_feeling_down_watch_Q2JCTCQzgKM|https://www.youtube.com/watch?v=Q2JCTCQzgKM"
  "how_billionaires_make_their_billions_LMlbWtUFa4E|https://www.youtube.com/watch?v=LMlbWtUFa4E"
  "launch_a_physical_product_brand_the_right_way_LvHDT0ZxSmw|https://www.youtube.com/watch?v=LvHDT0ZxSmw"
  "how_i_would_invest_1000_if_i_were_in_my_20s_dZ7xeVCYC5M|https://www.youtube.com/watch?v=dZ7xeVCYC5M"
  "how_i_hacked_my_brain_to_feel_gratitude_all_the_time_hrp3ehx_lJM|https://www.youtube.com/watch?v=hrp3ehx_lJM"
  "12m_followers_in_6_months_my_content_marketing_strategy_reve_MD5-HByRxoA|https://www.youtube.com/watch?v=MD5-HByRxoA"
  "my_employees_dont_take_my_business_seriouswhy_JDkiAxSd5Ms|https://www.youtube.com/watch?v=JDkiAxSd5Ms"
  "10_keys_to_a_terrible_business_partnership_guaranteed_JShQ8BX08rs|https://www.youtube.com/watch?v=JShQ8BX08rs"
  "applying_100m_offers_to_e-commerce_im_pissed_NA61omfYgvI|https://www.youtube.com/watch?v=NA61omfYgvI"
  "the_simple_managerial_framework_that_changed_my_business_IMowPVgcWbA|https://www.youtube.com/watch?v=IMowPVgcWbA"
  "biggest_mistake_that_stopped_me_from_getting_past_30m_EUW3rMp-Uvg|https://www.youtube.com/watch?v=EUW3rMp-Uvg"
  "2_types_of_business_risk_and_the_one_i_choose_every_time_A9qHKjFPJ-E|https://www.youtube.com/watch?v=A9qHKjFPJ-E"
  "why_i_chose_to_disappoint_my_dad_To8jcTDwcxc|https://www.youtube.com/watch?v=To8jcTDwcxc"
  "why_i_dont_follow_my_feelings_6nkoXslz_pI|https://www.youtube.com/watch?v=6nkoXslz_pI"
  "how_i_learned_to_sellmindset_training_AGCtZmgJ1JA|https://www.youtube.com/watch?v=AGCtZmgJ1JA"
  "i_lived_on_0_income_for_3_years_growing_my_business_x1CtbsEqxW0|https://www.youtube.com/watch?v=x1CtbsEqxW0"
  "how_to_close_the_sale_when_the_prospect_says_i_left_my_walle_Ul87yrDKZ78|https://www.youtube.com/watch?v=Ul87yrDKZ78"
  "100m_ceo_shares_the_secret_to_the_fastest_road_to_financial_OVhNSzFSoZs|https://www.youtube.com/watch?v=OVhNSzFSoZs"
  "top_10_fast_business_lessons_from_my_last_10_years_WsYgWC7NmO8|https://www.youtube.com/watch?v=WsYgWC7NmO8"
  "2_signs_that_you_will_become_wealthy_one_day_pmxzhyF0NrE|https://www.youtube.com/watch?v=pmxzhyF0NrE"
  "how_i_built_a_12m_follower_audience_in_6_months_WrCt0R3FBFs|https://www.youtube.com/watch?v=WrCt0R3FBFs"
  "100m_ceo_teaches_the_quickest_way_to_increase_revenue_withou_FXzDLLdxsCk|https://www.youtube.com/watch?v=FXzDLLdxsCk"
  "keynote_small_to_big_the_big_4_customer_acquisition_models_XwZH-lOKG9c|https://www.youtube.com/watch?v=XwZH-lOKG9c"
  "why_losing_friends_is_normal_rl_IkHyKHJI|https://www.youtube.com/watch?v=rl_IkHyKHJI"
  "i_sold_8_businesses_by_age_32_heres_how_pWbSl7d0tEc|https://www.youtube.com/watch?v=pWbSl7d0tEc"
  "i_emailed_myself_all_of_my_failures_for_the_last_5_yearsthis_QQGHCG8d1So|https://www.youtube.com/watch?v=QQGHCG8d1So"
  "the_best_sales_training_on_the_internet_NcD2t9qt-fM|https://www.youtube.com/watch?v=NcD2t9qt-fM"
)

LOG=ingest_hormozi.log
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

    # Patch .meta.json with content_category + creator
    meta="transcripts/${slug}.meta.json"
    if [[ -f "$meta" ]]; then
      python3 -c "
import json
p = '$meta'
d = json.load(open(p))
d['content_category'] = 'alex_hormozi'
d['creator'] = 'Alex Hormozi'
d['channel_handle'] = '@AlexHormozi'
d['brand'] = 'Acquisition.com'
json.dump(d, open(p,'w'), indent=2)
"
    fi
  else
    echo "[$i/$total] FAIL: $slug — see $LOG tail" | tee -a "$LOG"
    fail=$((fail+1))
  fi
done

echo "======================================" | tee -a "$LOG"
echo "M4 Hormozi done: ok=$ok skip=$skip fail=$fail total=$total" | tee -a "$LOG"
```

Then run:

```bash
chmod +x ingest_hormozi.sh
./ingest_hormozi.sh
```

Unattended. ~12-14 h. Each transcript + `.meta.json` uploads to GitHub as its own commit directly to `origin/main` (no PR).

---

## 4. `.meta.json` sidecar — content category

Every `.meta.json` sidecar written by `scribe.sh` for this batch MUST carry:

```json
{
  "content_category": "alex_hormozi",
  "creator": "Alex Hormozi",
  "channel_handle": "@AlexHormozi",
  "brand": "Acquisition.com"
}
```

The ingest loop patches the sidecar inline after each `OK` (see §3). This tags the batch for filtering at Atlas retrieval time.

---

## 5. Verify + notify

```bash
tail -20 ingest_hormozi.log
echo "---"
grep -c "OK (" ingest_hormozi.log
grep -c "FAIL:" ingest_hormozi.log
```

If `ok >= 240` (of 270, allowing for a few deleted/age-gated videos) print `M4 HORMOZI INGEST COMPLETE` and stop. If `ok < 240`, print the failing slugs and stop.

**Do NOT run `updateDB.sh`.** Knowledge extraction runs separately after this batch is confirmed complete.

---

## 6. What NOT to do

- Do **not** modify `scribe.sh`, `process.py`, or `qwen_transcribe.py`.
- Do **not** run `updateDB.sh`.
- Do **not** commit anything outside `transcripts/`.
- Do **not** delete `~/Desktop/scribe-m4-hormozi/` until the human confirms.
- Do **not** hunt for videos beyond the 270 in the array above unless the human explicitly asks — the list is dedup'd against the existing corpus, so scanning again is wasted work.
- If you hit a decision point not covered here, **stop and print the situation**.

---

## Rollback

```bash
cd ~/Desktop/scribe-m4-hormozi
git status
git reset --hard origin/main    # nuclear — reverts to remote
```

---

**End of instructions. Execute in order. Ping the human on completion or first blocker.**

