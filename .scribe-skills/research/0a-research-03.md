# 0a-research-03 — Prompt Caching: TTL, Pricing, Minimums, and Structured-Output Interaction

**Agent:** 03 of 10  
**Date:** 2026-06-16  
**Sources fetched:** 3 URLs  
**Question:** Confirm cache TTL options, exact pricing for Haiku 4.5 / Sonnet 4.6, minimum token threshold, caching + structured output interaction, and any 2025-2026 changes that affect PLAN.md §1 cost model.

---

## Sources

1. `https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching` — official prompt caching docs (redirected from docs.anthropic.com)
2. `https://platform.claude.com/docs/en/docs/about-claude/pricing` — official pricing page (redirected from docs.anthropic.com)
3. Web search: "Anthropic prompt caching pricing TTL 2026 Haiku 4.5 Sonnet 4.6 cache read write"

---

## Findings

### (a) Cache TTL options and exact pricing

Two TTL durations are available:

| Cache operation | Multiplier | Duration |
|:----------------|:-----------|:---------|
| 5-minute cache write | **1.25×** base input | 5 minutes |
| 1-hour cache write | **2×** base input | 1 hour |
| Cache read (hit) | **0.1×** base input | Same duration as preceding write |

Configured via `cache_control`:
- 5-minute (default): `{"type": "ephemeral"}`
- 1-hour: `{"type": "ephemeral", "ttl": "1h"}`

**Exact per-model figures (from official pricing table):**

| Model | Base Input | 5m Cache Write | 1h Cache Write | Cache Read | Output |
|-------|-----------|---------------|---------------|-----------|--------|
| Claude Haiku 4.5 | $1 / MTok | **$1.25 / MTok** | $2 / MTok | **$0.10 / MTok** | $5 / MTok |
| Claude Sonnet 4.6 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |

**PLAN.md §1 numbers for Haiku 4.5 are exactly correct:**
- Cache read: $0.10/MTok ✓
- Cache write (5-min): $1.25/MTok ✓
- Base input: $1/MTok ✓
- Output: $5/MTok ✓

### (b) Minimum token threshold to be cacheable

| Model | Minimum tokens |
|-------|---------------|
| **Claude Haiku 4.5** | **4,096 tokens** |
| Claude Sonnet 4.6 | 1,024 tokens |
| Claude Sonnet 4.5 | 1,024 tokens |
| Claude Opus 4.5–4.8 | 1,024–4,096 tokens (varies) |

**Critical note for PLAN.md §1:** Haiku 4.5 requires **4,096 tokens minimum** to be cacheable — not 1,024 (which applies to Sonnet). This is the highest threshold of the current model lineup. System prompts shorter than ~4k tokens will silently fail to cache on Haiku 4.5 (no error; `cache_creation_input_tokens` returns 0).

PLAN.md §1 states "System prompts ≥ 3000 tokens that are shared by 3+ concurrent agents → mark as cached." A 3,000-token system prompt will **not** cache on Haiku 4.5. The threshold for Haiku must be ≥ 4,096 tokens.

### (c) Caching interaction with structured output (tool-use vs JSON-mode)

**Tool use:**
- Tool definitions (the `tools` array) **can be cached**. Place `cache_control` on the last tool in the array; everything up to that point is cached.
- `tool_use` content blocks and `tool_result` blocks in messages are also cacheable.
- Changes to `tool_choice` parameter **invalidate** both system and message caches.
- Tool use adds fixed system-prompt overhead per model (Haiku 4.5: 496 tokens for `auto`/`none`, 588 tokens for `any`/`tool`). These overhead tokens count toward the 4,096-token minimum.

**JSON mode (structured output without tools):**
- The docs do not explicitly address `response_format` / JSON mode interaction with caching.
- Output format changes are not listed among cache-invalidating parameters (the listed invalidators are: system prompt changes, tool changes, images, `tool_choice`, `thinking` parameters).
- Inference: switching JSON mode on/off likely invalidates the cache if it changes the system prompt or a tool definition. If JSON mode is implemented purely as an output constraint without touching the cached prefix, cache hits should still occur.

**Extended thinking:**
- Thinking blocks cannot be explicitly marked with `cache_control` but are automatically cached when they appear in prior assistant turns.
- On Haiku (all versions), thinking blocks are stripped from context when non-tool-result user content is added, which **invalidates the cache**. This is a Haiku-specific limitation. Sonnet 4.6+ preserves thinking blocks by default.

### (d) 2025-2026 changes affecting §1 cost model

**February 5, 2026 — Workspace-level cache isolation:**
- Caches now isolated **per workspace** (previously per organization) on Claude API, Claude Platform on AWS, and Microsoft Foundry.
- Amazon Bedrock and Vertex AI remain organization-level.
- Impact for this project: if Scribe uses a single workspace, no behavioral change. If we split workspaces by environment (dev/prod), cache writes in one will not hit in the other.

**Pricing: no changes in 2025-2026.** The multipliers (1.25× write, 0.1× read, 2× for 1-hour) and absolute per-model prices are current and match PLAN.md §1 exactly.

**1-hour TTL now available (previously not in PLAN.md §1):**
- PLAN.md §1 notes "1 hour available" but the cost model only uses 5-minute TTL. The 1-hour write costs 2× base input ($2/MTok for Haiku), so it pays off only after ≥2 cache reads within the hour.
- For agentic workflows where agents launch more than 5 minutes apart (common in multi-phase rebuilds), 1-hour TTL is strictly better if the system prompt is reused 2+ times per hour.

**New tokenizer note:**
- Opus 4.7 and later use a new tokenizer that may use up to 35% more tokens for the same text. Haiku 4.5 and Sonnet 4.6 are unaffected by this.

---

## Gaps / uncertainties

- JSON-mode cache invalidation behavior is not explicitly documented; empirical test needed.
- The 4,096-token minimum for Haiku 4.5 is confirmed but not explained in terms of why it differs from Sonnet. Likely an architectural constraint.
- No documentation on whether the `cache_control` field on tool definitions works differently when `tool_choice: "any"` vs `"auto"`.

---

## Bottom Line

**PLAN.md §1 pricing numbers are correct** for Haiku 4.5: $0.10/MTok read, $1.25/MTok write, $1/MTok base input, $5/MTok output. The $7 total budget is not materially affected by pricing.

**One material error in PLAN.md §1:** The cache strategy states "System prompts ≥ 3000 tokens → mark as cached." This will silently fail on Haiku 4.5 because the minimum cacheable threshold is **4,096 tokens**, not 3,000. Any system prompt under ~4.1k tokens will not cache, and the cache-hit cost savings in the §1 per-agent cost envelopes will not materialize for those agents. The fix is simple: pad or extend Haiku system prompts to exceed 4,096 tokens, or consolidate prompt content to reach that floor.

**Secondary note:** For multi-phase workflows where agents run >5 min apart, consider 1-hour TTL writes ($2/MTok) instead of 5-minute ($1.25/MTok). Break-even is 2 reads within the hour; at 3+ reads it becomes cheaper overall.
