# Scribe token-budget prediction — read before firing any Workflow

**Purpose:** Predict, within ~20%, how many 5h-window percentage points a Phase 3a/3b/4/6 Workflow will burn BEFORE you fire it. Every prior "surprise cap hit" traces to skipping this math.

Grounded in empirical measurements from Wave 1 (2026-06-21, Rory shorts+longs), Wave 2 (2026-06-21, extended Sonnet longs), and the 2026-07-11 miscalc (task `t-o0fev7`).

---

## 1. Per-agent token cost (measured)

| Model | Duration bucket | Avg output tokens/agent | Source |
|---|---|---|---|
| Haiku | shorts (<90s) | ~50k | Wave 1: 51 agents / 2.73M / 161s |
| Haiku | medium (90–180s) | ~55k | interpolation Wave 1 |
| Haiku | medium (180–600s) | ~65k | scaled from Wave 1 short avg |
| Sonnet | shorts (<90s) | ~75k (wasteful — use Haiku) | 2026-07-11 first-agent test: 79k |
| Sonnet | medium (90–600s) | ~90k | Wave 2 wave-1 sample |
| Sonnet | long (600–1800s) | ~115k | Wave 2: 15 agents / 1.67M / 351s |
| Sonnet | long (≥1800s) | ~130k | Wave 2 wave-2 outliers |

**Rule of thumb:** if `duration_seconds < 180` → **Haiku**, else Sonnet. Do not use Sonnet on shorts — same wall-clock, ~1.5× tokens, no measurable claim-density improvement.

---

## 2. Main-loop overhead multiplier

Workflow agents burn against the same 5h window as your top-level model's tool-call turns. Empirical multiplier:

| Top-level model | Multiplier vs raw agent tokens |
|---|---|
| Haiku 4.5 | ~1.05× |
| Sonnet 4.6 | ~1.2× |
| **Opus 4.7 / sonnet[1m]** | **~1.5×** (heavy Bash/Read/Edit turns compound quickly) |

The 2026-07-11 miscalc: estimated 22M raw Sonnet tokens → real burn 68 5h points, implying an *effective* per-token rate of ~324k tokens per point (vs Wave 2's ~500k). Backing out the multiplier: 22M × 1.5 = 33M effective → 33M / 500k = 66 points. **Matches observed 68.**

**Always multiply your raw-token estimate by the top-level model's multiplier before comparing to the window budget.**

---

## 3. 5h window budget

A full 5h window = **~50M effective output tokens** = 100 percentage points.  Empirical: 1 point ≈ 500k effective tokens.

Halt threshold (CLAUDE.md standard): **80%**. That leaves 10M effective tokens headroom — enough for one Opus reasoning turn plus final-phase Haiku work.

---

## 4. Pre-fire checklist

Run this before every `Workflow({scriptPath: ...})`:

```bash
# 1. Current usage
python3 -c "import json,datetime; d=json.load(open('/Users/pranavgupta/.claude/usage_current.json')); r=d['rate_limits']; f=r['five_hour']; print(f\"5h={f['used_percentage']}% resets {datetime.datetime.fromtimestamp(f['resets_at']).strftime('%H:%M PT')}\")"

# 2. Estimate raw agent tokens
python3 <<EOF
import json
sources = json.load(open('.forge_scratch/scribe_phase3a/chunkX.json'))['sources']
def est(s):
    d = s.get('duration', 0)
    # Adjust model+bucket per section 1
    if d < 180: return 50_000   # Haiku
    if d < 600: return 90_000   # Sonnet med
    return 130_000              # Sonnet long
raw = sum(est(s) for s in sources)
overhead = 1.5  # for Opus 4.7 top-level
effective = raw * overhead
points = effective / 500_000
print(f"raw={raw/1e6:.1f}M  effective={effective/1e6:.1f}M  est_burn={points:.0f} pts")
EOF
```

**Decision matrix:**

| current_5h + est_burn | Action |
|---|---|
| ≤ 70% | Fire immediately |
| 70–80% | Fire, but split into smaller subchunks with usage-check between |
| 80–95% | Wait for next reset (`resets_at` epoch decodes to clock time — never guess) |
| ≥ 95% | **DO NOT FIRE.** Wait for reset even if urgent. |

---

## 5. Chunking discipline

If your total estimate exceeds a single 5h window (>~40M effective tokens):

1. **Split by model+duration first** — one chunk per bucket. Duration ascending inside each bucket lets partial completions leave a graceful queue.
2. **Cap each chunk at ~35M effective tokens** = ~70 5h points. Leaves 30% headroom for main-loop overhead surprises.
3. **Between chunks, check `usage_current.json`** and either fire the next or wait for reset.
4. Concurrency is auto-capped at `min(16, cpu-2)` per workflow; do not manually parallelize beyond that.

Example split for 850 mixed transcripts:
- Chunk A: Haiku for all `<180s` items → ~15M raw × 1.5 = 22M effective → ~44 points. Safe.
- Chunk B: Sonnet for all `180–600s` items → ~28M raw × 1.5 = 42M effective → **too big, split B1/B2**.
- Chunk C: Sonnet for all `≥600s` items → ~32M raw × 1.5 = 48M effective → **too big, split C1/C2**.

---

## 6. Active-monitoring sub-agent pattern

For any single-chunk workflow expected to burn >30 5h points, spawn a Haiku "watchdog" sub-agent in parallel that polls `~/.claude/usage_current.json` every 30–60 seconds and writes flag files at defined thresholds. Main session (or a manual kill) reads those flags between agent batches.

Breakpoint reference (see `~/.forge/scratch/t-scribe-mgr/tracker/` for the current template):

| 5h % | Watchdog action | Main-session action |
|---|---|---|
| 60% | log `NOTICE` | continue |
| 75% | log `SLOW` + write flag `SLOW_DOWN` | halt Bash/Read spam, minimize turn count |
| 85% | write flag `HALT_SOON` | finish current subchunk, defer next |
| 95% | write flag `HALT_NOW` + exit | `TaskStop` the workflow immediately |

The watchdog cannot itself call `TaskStop` — it only surfaces state. Main session decides.

---

## 7. Post-mortem: the 2026-07-11 miscalc (canonical example)

Fired 299 Sonnet Phase 3a agents. Predicted 22.4M tokens (~45 5h points). Reality: 68 points burned before `TaskStop` at 98/299 done.

Errors:
1. **No overhead multiplier applied.** 22M × 1.5 (Opus top-level) = 33M effective → 66 points. Matches reality.
2. **Sonnet on shorts.** 101 items were <90s. Should have been Haiku at ~50k, not Sonnet at ~75-115k. Cost 5-6M unnecessary tokens.
3. **No pre-fire usage-halt planning.** Fired at 7% expecting to land at ~50%. Actual landing had been ~75% before kill.

Preserved outcomes despite the kill:
- 389 valid extraction files on disk (up from 285).
- Zero data loss — `TaskStop` fired only after in-flight agents completed their JSON writes.

Rules distilled from this incident:
- Never estimate without §2's multiplier.
- Never send `<180s` items to Sonnet.
- Never fire a chunk that could push 5h past 80% without a mid-run halt plan.

---

## 8. When measurements need to be re-baselined

Re-run Wave-2-style calibration every time one of these changes:
- Anthropic model version (Haiku 4.5 → 4.6, Sonnet 4.6 → 4.7, etc.)
- `CLAIM-DEFINITION.md` prompt structure (a major rewrite changes per-agent output length)
- Top-level model (was Opus 4.6, now 4.7 as of 2026-07-11)

Method: fire a controlled 30-agent workflow with mixed durations, measure delta on `usage_current.json.five_hour.used_percentage`, update the table in §1.
