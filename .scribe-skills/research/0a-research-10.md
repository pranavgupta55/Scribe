# 0a-Research-10: Haiku 4.5 for Structured-Extraction Tasks

**Question:** How does Claude Haiku 4.5 differ from Sonnet 4.6 for structured-extraction tasks? Covers: (a) practitioner batch sizes, (b) output-JSON reliability past N items, (c) long-context failure modes, (d) structured-output tricks, (e) cache hit/miss math.

---

## (a) Practitioner-reported optimal batch sizes for JSON-extraction workloads

No public benchmark directly tests the exact "items per batch → reliability" curve for Haiku 4.5 JSON extraction. The closest practitioner signals are:

- **One document per Batch API request** is the pattern recommended for production document-extraction pipelines. The Claude Batch API is designed around many small requests (up to 100,000 per batch submission), not one large request containing many items. ([NerdLevelTech](https://nerdleveltech.com/claude-batch-api-bulk-extraction-typescript-tutorial))
- A practical token budget for one extraction call is ~500 input + ~200 output tokens per document. For a richer prompt (system + few-shots + schema), practitioners allocate 2,000–5,000 tokens for the system side and 1,000–2,000 for output, with user input in the 500–2,000 range. ([PADISO](https://www.padiso.co/blog/haiku-4-5-batch-processing-patterns-pitfalls/), [Markaicode](https://markaicode.com/claude-haiku-4-5-high-volume-production-workloads/))
- The throughput ceiling: Tier-2 accounts see ~4M input TPM on Haiku 4.5, supporting ~500 concurrent requests/second. Concurrency math: at 500-in/200-out tokens, ~46 concurrent workers saturates 70% of TPM quota. ([Markaicode](https://markaicode.com/claude-haiku-4-5-high-volume-production-workloads/))
- **For multi-item-per-call payloads** (the pattern PLAN uses for connection labeling at 60 pairs, cluster verification at 25 clusters): no practitioner has published a controlled ablation. The tool-use blocking guidance says tool_use enforcement "isn't suitable for streaming or arrays exceeding ~100 items." The 80-pair reliability drop noted in PLAN.md is consistent with the general token-pressure pattern but is not independently corroborated by external sources. ([Thomas Wiegold](https://thomas-wiegold.com/blog/claude-api-structured-output/))

**Summary for PLAN:** PLAN's 60-pair / 25-cluster sizing is within the safe zone per available practitioner data. The "beyond 80 pairs, output JSON reliability drops" boundary in PLAN's table is a reasonable working estimate; no public source contradicts it.

---

## (b) How output-JSON reliability degrades past N items per batch

### What structured outputs guarantee (and don't)

With native structured outputs (GA on Haiku 4.5 as of 2026-02-04), the constrained-decoding grammar makes it **syntactically impossible** to emit invalid JSON tokens. Schema compliance is enforced at the token-generation level. ([Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [Claude blog](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform))

Two overrides that break schema compliance even with structured outputs:
1. **`stop_reason == "refusal"`** — safety refusal; HTTP 200 but non-schema content.
2. **`stop_reason == "max_tokens"`** — truncation mid-output; JSON is incomplete.

**The key failure mode for large-N batches is not JSON syntax but `max_tokens` truncation.** As the item count in a single call grows, the output token budget must grow proportionally. If you set `max_tokens` to a lazy default (e.g., 4,096), large arrays are silently truncated.

Practitioner guidance:
- "Set `max_tokens` to the actual ceiling you need — not 4096 as a lazy default. For single-label classification, 10–20 tokens is enough." ([Markaicode](https://markaicode.com/claude-haiku-4-5-high-volume-production-workloads/))
- Monitor `stop_reason` on every response. For a 60-pair JSON array output (~6k output tokens as estimated in PLAN), set `max_tokens` to at least 8,000–10,000 to avoid clipping.
- Haiku 4.5's max output is **64,000 tokens**, so the ceiling is not a hard constraint at PLAN's batch sizes.

### Haiku vs Sonnet for complex JSON extraction

| Task complexity | Haiku 4.5 accuracy | Sonnet 4.6 accuracy | Gap |
|---|---|---|---|
| Simple extraction (forms, defined fields) | 96–98% | 98–99.5% | ~2% |
| Complex extraction (invoices, contracts) | 88–92% | 94–97% | ~5–7% |

Source: [PADISO routing decision tree](https://www.padiso.co/blog/claude-sonnet-4-6-vs-haiku-4-5-model-routing-decision-tree/)

Specific Haiku 4.5 weakness: **"Haiku occasionally omits optional nested fields under token pressure."** ([Thomas Wiegold](https://thomas-wiegold.com/blog/claude-api-structured-output/)) This is not a JSON-syntax issue but a semantic completeness issue — the output is valid JSON but misses optional fields when the context is long or the model deprioritizes them.

For PLAN's workloads (connection labeling = well-defined 5-field schema per pair; cluster verification = enum + list schema): Haiku 4.5 is appropriate. The PLAN schemas are not deeply nested and the fields are all required, so the optional-field omission risk is low.

---

## (c) Known failure modes on long contexts

### Context degradation: the 100k token threshold

Haiku 4.5's context window is 200k tokens, but performance is not uniform across the window:

- "Claude-4.5-haiku exhibits early instability, with extraction and inference performance becoming volatile **beyond the 100k token mark**." (Referenced in search results from needle-in-haystack evaluations)
- The "lost in the middle" phenomenon affects all models: facts embedded in the middle of long contexts are retrieved less reliably than facts at the start or end. Academic study (arxiv 2601.02023) confirms this across all tested LLM architectures.
- Chroma Research (2025) tested 18 production LLMs on multi-hop reasoning over 10k–500k tokens: all showed monotonically decreasing F1 as context grew, with steepest degradation in the 100k–500k range.
- Sonnet 4.6 has a **1M token context window** vs Haiku 4.5's 200k. For very long document chains, Sonnet 4.6 degrades more gracefully.

### Practical implications for PLAN

PLAN's per-agent payloads are well within the safe zone:
- Connection labeling: ~15k tokens input (60 pairs × ~250 tokens each)
- Cluster verification: ~6k tokens input (25 clusters × ~240 tokens each)
- Extraction v2: 1 transcript per agent; long transcripts are sub-windowed inside the agent

The risk zone (>100k token context in a single call) does not apply to any PLAN phase as designed. The 200k window is not being approached.

### Other long-context failure modes
- "Still stalls on very long terminal sequences" (agentic use, not extraction) ([Medium review](https://medium.com/@leucopsis/claude-haiku-4-5-review-4ac12a103275))
- Instruction drift on very long prompts: "Haiku can skip steps, reorder instructions, or ignore minor constraints — especially in long prompts." ([Sider.ai](https://sider.ai/blog/ai-tools/claude-haiku-4_5-in-production-surviving-the-quiet-genius-and-its-sneaky-gotchas)) Mitigation: put the JSON schema at the top, include a single canonical example, ask for only that format with no commentary.

---

## (d) Specific structured-output tricks

### Ranked by reliability (highest to lowest)

1. **Native structured outputs (`output_config.format` with `json_schema`)** — GA on Haiku 4.5 as of 2026-02-04. Uses constrained decoding (grammar compiled from schema). The model cannot emit tokens that violate the schema. 24-hour grammar cache means first-request compilation (100–300ms overhead) is amortized across the batch. **This is the correct approach for PLAN.** ([Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs))

2. **Forced tool use (`tool_choice: {"type": "tool", "name": "..."}`)** — Same constrained-decoding pipeline, but triggers via a named tool invocation rather than a response-format parameter. Eliminates free-text responses entirely. Slightly more complex API contract; blocks until completion so not suitable for streaming. Limit: 20 strict tools per request, 24 optional parameters total, 16 `anyOf` union types. ([Markaicode JSON mode guide](https://markaicode.com/claude-45-json-mode-structured-output/), [Anthropic strict tool use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use))

3. **Prefilling (assistant turn prefill)**  — Start the assistant turn with `{"` to force JSON opening. This is a soft nudge, not grammar enforcement. Unreliable for complex schemas. Use only as a fallback for contexts where native structured outputs are unavailable (e.g., older model versions or certain proxy layers).

4. **Prompt-only constraints** — ~95% success for simple schemas. Haiku 4.5 follows JSON format instructions well when the schema is short and fields are all required. Falls to ~80% for complex schemas or long input contexts. Use only when native structured outputs are unavailable.

### Key schema design rules for Haiku 4.5
- Mark only critical fields as required; but do mark all fields you actually need as required (Haiku omits optional fields under token pressure).
- Flatten nested structures — deeply nested schemas compound grammar-state complexity and increase compilation time.
- Avoid unsupported schema features (these cause 400 errors): recursive schemas, external `$ref`, `minimum`/`maximum` numeric constraints, `minLength`/`maxLength` string constraints.
- Each optional parameter roughly doubles the grammar state space; PLAN's schemas should prefer required fields.

### Structured outputs + caching interaction
Changing `output_config.format` invalidates the **prompt cache** for that request thread (though the 24-hour grammar cache persists separately). Keep schemas byte-identical across all concurrent agents to maximize cache hits. ([ApiForge Medium](https://apiforgecom.medium.com/claude-api-prompt-caching-with-structured-outputs-the-missing-piece-in-the-docs-f6c0ae6d1df8))

---

## (e) Cache hit/miss math for PLAN

### Authoritative minimum token thresholds (from docs)

| Model | Min tokens for caching |
|---|---|
| Claude Haiku 4.5 | **4,096 tokens** |
| Claude Sonnet 4.6 | **1,024 tokens** (confirmed — not 2,048) |
| Claude Haiku 3.5 | 2,048 tokens |
| Claude Opus 4.6/4.5 | 4,096 tokens |

Source: [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [apiyi.com troubleshooting guide](https://help.apiyi.com/en/claude-prompt-caching-not-hit-minimum-token-troubleshooting-en.html), [StartDebugging](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/)

**Critical finding for PLAN:** PLAN.md §1 states "System prompts ≥ 3000 tokens that are shared by 3+ concurrent agents → mark as cached." **This will silently fail for Haiku 4.5** because 3,000 tokens is below the 4,096-token minimum. No error is returned; `cache_creation_input_tokens` and `cache_read_input_tokens` will both be 0.

### Cache pricing and break-even

All pricing per the Anthropic model table (current as of docs retrieval):

| Cost category | Haiku 4.5 rate |
|---|---|
| Input (non-cached) | $1.00 / MTok |
| Output | $5.00 / MTok |
| Cache write (5m TTL) | $1.25 / MTok (1.25× base) |
| Cache write (1h TTL) | $2.00 / MTok (2.0× base) |
| Cache read | $0.10 / MTok (0.10× base) |

**Break-even reads:**
- 5-minute TTL: break-even after **0.28 reads** (write premium = 0.25× base; read savings = 0.90× base; net positive after just 1 read beyond the write)
- 1-hour TTL: break-even after **1.11 reads** (write premium = 1.0× base; need >1 additional read to recover)

Source: [StartDebugging](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/)

### Concrete PLAN math: connection-labeling batch

System prompt (NODE-QUALITY-RUBRIC + CLAIM-DEFINITION + FEW-SHOT + CONN_PROMPT_V2): must be ≥ 4,096 tokens to cache on Haiku 4.5.

If system prompt = 4,096 tokens (meeting minimum):
- Cache write: 4,096 × $1.25/M = **$0.0051** per first agent
- Cache read: 4,096 × $0.10/M = **$0.00041** per subsequent agent
- Per-batch input: 15,000 × $1/M = **$0.015**
- Per-batch output: 6,000 × $5/M = **$0.030**
- **First agent total: $0.0051 + $0.015 + $0.030 = $0.0501**
- **Each subsequent agent (within 5-min window): $0.00041 + $0.015 + $0.030 = $0.04541**

This is very close to PLAN's estimate of $0.046 per agent — consistent.

### Cache hit rate risk factors

1. **System prompt below 4,096 tokens** — silent cache miss on Haiku 4.5. PLAN's current threshold of 3,000 tokens is **too low**. Extend system prompt with full rubric sections, FEW-SHOT.md, and schema definitions to reach 4,096+.
2. **Concurrent agents must fire within the 5-minute cache TTL window.** If agent dispatch is sequential with gaps >5 min between agents, each write is wasted. With 30 connection-labeling agents dispatched in parallel, the TTL window is satisfied trivially.
3. **Byte-identical system prompts required.** Any per-agent variable content (e.g., agent index, timestamp) in the cached prefix invalidates the cache. Keep all variable content in the user message (after the cache breakpoint).
4. **Reported 0% cache hit rate on Haiku 4.5** in some production pipelines — root cause in all reported cases was system prompt below the 4,096-token minimum. ([mikenoe.com](https://mikenoe.com/posts/prompt-caching-classivore/), [GitHub issue zeroclaw-labs/zeroclaw#3977](https://github.com/zeroclaw-labs/zeroclaw/issues/3977))
5. **Batch API caching caveat:** Cache pre-warming with `max_tokens: 0` is not supported in the Batch API. Use regular synchronous requests to warm the cache if needed, or rely on organic warming from the first batch agent.

### Haiku 4.5 vs Sonnet 4.6 cache threshold asymmetry

Sonnet 4.6 requires only 1,024 tokens minimum vs Haiku 4.5's 4,096. This means:
- Small system prompts (1,024–4,095 tokens) can be cached on Sonnet but NOT on Haiku.
- For PLAN's Sonnet web-research agents, caching is available with much smaller prompts.
- For Haiku agents, the system prompt must be padded to 4,096+ tokens via rubric, few-shot examples, and schema definitions. This padding is useful content anyway.

---

## Haiku 4.5 vs Sonnet 4.6 summary for PLAN's extraction tasks

| Dimension | Haiku 4.5 | Sonnet 4.6 |
|---|---|---|
| Cost | $1/$5 per MTok | $3/$15 per MTok (3× more) |
| Context window | 200k tokens | 1M tokens |
| Max output | 64k tokens | 64k tokens |
| Simple extraction accuracy | 96–98% | 98–99.5% |
| Complex extraction accuracy | 88–92% | 94–97% |
| Cache minimum threshold | **4,096 tokens** | **1,024 tokens** |
| Speed | Fastest | Fast |
| Long-context stability | Volatile >100k tokens | More stable (1M window) |
| Structured output (GA) | Yes (since 2026-02-04) | Yes |
| Optional field omission risk | Present under token pressure | Lower |

For PLAN's workloads (connection labeling ~15k input, cluster verification ~6k input, extraction v2 ~1 transcript), Haiku 4.5 is the right choice: payloads are well within the stable context range, schemas can be designed with all required fields, and the 3× cost difference is material at 137 agents.

---

## Bottom Line: Revisions to PLAN.md §1 batch-size table

### Required revision 1: Cache minimum token threshold

**Current PLAN.md §1:** "System prompts ≥ 3000 tokens that are shared by 3+ concurrent agents → mark as cached."

**Revision:** Change to **≥ 4,096 tokens** (Haiku 4.5's minimum cacheable prefix length). A 3,000-token system prompt on Haiku silently skips caching with no error or warning. Every Haiku agent's system prompt must reach 4,096 tokens to qualify. Embed full rubric sections (NODE-QUALITY-RUBRIC §A+C), CLAIM-DEFINITION, FEW-SHOT.md, and the output schema inline to meet this floor. Verify by checking `cache_creation_input_tokens > 0` in the first agent's response.

### Required revision 2: Use `max_tokens` calibration per batch size

**Current PLAN.md §1 per-agent cost envelope:** "Per-batch output: 6k tokens × $5/M = $0.030"

**Revision:** Add a note: set `max_tokens` to **1.5× the estimated output token count** for large-array outputs. For 60-pair connection labeling (est. 6k output tokens), set `max_tokens=9000`. Truncated JSON arrays (`stop_reason == "max_tokens"`) are a silent data-loss failure — the output is valid JSON up to the truncation point. Monitor `stop_reason` in all agent outputs.

### Recommended revision 3: Use native structured outputs, not prompt-only JSON

**Current PLAN.md §3a:** "Output schema is enforced with a Pydantic-style schema in the prompt and rejected JSON triggers a retry."

**Revision:** Use `output_config.format` with a `json_schema` (or `client.messages.parse()` with a Pydantic model) instead of prompt-only enforcement. Native structured outputs (GA on Haiku 4.5 since 2026-02-04) eliminate the retry loop for syntax errors. The 24-hour grammar cache means compilation overhead (~100–300ms) is paid once per schema. This simplifies Phase 3a/4 agent code and removes the need for a JSON-repair retry loop.

### No revision needed: batch sizes (60 pairs / 25 clusters)

The 60-pair and 25-cluster sizes fall comfortably within the safe operating zone:
- Both are well under the ~100-item limit where tool_use blocking becomes a concern.
- Input payloads (~15k and ~6k tokens) are well under the 100k-token context instability threshold.
- No practitioner data contradicts these choices; the 80-pair reliability boundary in PLAN is a reasonable empirical anchor.

---

## Sources

1. [Anthropic structured outputs docs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
2. [Claude structured outputs blog post](https://claude.com/blog/structured-outputs-on-the-claude-developer-platform)
3. [Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
4. [Anthropic models overview (latest)](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-5)
5. [PADISO: Sonnet 4.6 vs Haiku 4.5 routing decision tree](https://www.padiso.co/blog/claude-sonnet-4-6-vs-haiku-4-5-model-routing-decision-tree/)
6. [PADISO: Haiku 4.5 batch processing patterns and pitfalls](https://www.padiso.co/blog/haiku-4-5-batch-processing-patterns-pitfalls/)
7. [Markaicode: Claude Haiku 4.5 high-volume production workloads](https://markaicode.com/claude-haiku-4-5-high-volume-production-workloads/)
8. [Markaicode: Claude 4.5 JSON mode structured output](https://markaicode.com/claude-45-json-mode-structured-output/)
9. [Thomas Wiegold: Claude API structured output guide](https://thomas-wiegold.com/blog/claude-api-structured-output/)
10. [StartDebugging: How to add prompt caching and measure hit rate](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/)
11. [Mike Noe: Prompt caching hit rate zero in labeling pipeline](https://mikenoe.com/posts/prompt-caching-classivore/)
12. [ApiForge Medium: Claude API prompt caching with structured outputs](https://apiforgecom.medium.com/claude-api-prompt-caching-with-structured-outputs-the-missing-piece-in-the-docs-f6c0ae6d1df8)
13. [apiyi.com: Prompt caching troubleshooting and min token thresholds](https://help.apiyi.com/en/claude-prompt-caching-not-hit-minimum-token-troubleshooting-en.html)
14. [Hidekazu Konishi: Anthropic Claude API prompt caching and token efficiency](https://hidekazu-konishi.com/entry/anthropic_claude_api_prompt_caching_and_token_efficiency.html)
15. [Caylent: Claude Haiku 4.5 deep dive](https://caylent.com/blog/claude-haiku-4-5-deep-dive-cost-capabilities-and-the-multi-agent-opportunity)
16. [Medium (Barnacle Goose): Claude Haiku 4.5 review](https://medium.com/@leucopsis/claude-haiku-4-5-review-4ac12a103275)
17. [NerdLevelTech: Claude Batch API bulk extraction TypeScript tutorial](https://nerdleveltech.com/claude-batch-api-bulk-extraction-typescript-tutorial)
18. [UnpromptedMind: Claude Batch API large-scale processing](https://www.unpromptedmind.com/claude-batch-api-large-scale-processing/)
19. [Sider.ai: Claude Haiku 4.5 in production gotchas](https://sider.ai/blog/ai-tools/claude-haiku-4_5-in-production-surviving-the-quiet-genius-and-its-sneaky-gotchas)
20. [arXiv 2601.02023: Needle extraction and fact distribution in long-context LLMs](https://arxiv.org/pdf/2601.02023)
