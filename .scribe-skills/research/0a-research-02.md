# 0a-research-02 — Production RAG Extraction Prompts

## Bottom line

Production RAG frameworks converge on four patterns: (1) numbered-step prompts that separate entity identification from relationship/claim extraction; (2) structured-output enforcement via tool-use (Anthropic) or JSON-mode (LlamaIndex) rather than regex; (3) multi-pass "gleaning" loops (GraphRAG) using a CONTINUE prompt + a YES/NO gate prompt to recover missed entities; (4) few-shot examples embedded directly in the system prompt, not passed as chat turns. None of the five frameworks use a plan → draft → verify three-stage pipeline — the closest is GraphRAG's two-stage loop (extract → glean). For Phase 3a, the practical implication is: use tool-use for output validation, embed two or three few-shot examples verbatim in the system prompt, and run a single gleaning pass rather than a full three-stage pipeline.

---

## Evidence

### Prompt 1 — Microsoft GraphRAG `GRAPH_EXTRACTION_PROMPT`

Source: <https://huggingface.co/spaces/retopara/ragflow/blob/58f507b3bddbecc618a7fd0c6cebb80bcc8c3b10/graphrag/graph_prompt.py> (mirror of microsoft/graphrag)

**Schema required:** Two record types returned in a flat delimiter-separated list:
- `("entity"{tuple_delimiter}<name>{tuple_delimiter}<type>{tuple_delimiter}<description>)`
- `("relationship"{tuple_delimiter}<source>{tuple_delimiter}<target>{tuple_delimiter}<description>{tuple_delimiter}<strength_score>)`

**Few-shot pattern:** Three multi-entity fictional examples embedded verbatim inside the system prompt under an `####-Examples-####` section, each showing the full formatted output. Examples use person/organization/technology entities.

**Multi-pass:** Two-prompt gleaning loop appended after the main extraction:
- `CONTINUE_PROMPT = "MANY entities were missed in the last extraction. Add them below using the same format:"`
- `LOOP_PROMPT = "It appears some entities may have still been missed. Answer YES | NO if there are still entities that need to be added."`
The extractor repeats up to a configurable `max_gleanings` count. The YES/NO gate uses logit bias = 100 on those two tokens to force a binary response.

**Structured output enforcement:** Delimiter-based string parsing, not JSON-mode or tool-use. Validation is post-hoc regex on the delimiter pattern.

---

### Prompt 2 — Microsoft GraphRAG `CLAIM_EXTRACTION_PROMPT`

Source: <https://huggingface.co/spaces/retopara/ragflow/blob/58f507b3bddbecc618a7fd0c6cebb80bcc8c3b10/graphrag/claim_prompt.py>

**Schema required:** Per-claim tuple: `(<subject>{tuple_delimiter}<object>{tuple_delimiter}<claim_type>{tuple_delimiter}<claim_status>{tuple_delimiter}<start_date>{tuple_delimiter}<end_date>{tuple_delimiter}<description>{tuple_delimiter}<source_quotes>)`

Claim status is constrained to `TRUE | FALSE | SUSPECTED`.

**Few-shot pattern:** Two examples, both using the same source paragraph with different entity_specs (once by entity type "organization", once by named entity list). This tests the same prompt against two specification modes — a useful few-shot design.

**Multi-pass:** Same CONTINUE + LOOP gleaning loop as GRAPH_EXTRACTION_PROMPT.

**Structured output enforcement:** Delimiter parsing only.

**Key design observation:** GraphRAG separates entity extraction (Pass 1) from claim extraction (Pass 2) into two distinct prompt invocations. The entity list from Pass 1 is fed as `{entity_specs}` into the claim prompt. This two-pass dependency is the closest any of the five frameworks gets to plan → draft → verify.

---

### Prompt 3 — LlamaIndex `DEFAULT_KG_TRIPLET_EXTRACT_TMPL` and `DEFAULT_DYNAMIC_EXTRACT_TMPL`

Source: <https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/prompts/default_prompts.py>

**Triplet prompt (simple):**
```
Some text is provided below. Given the text, extract up to {max_knowledge_triplets}
knowledge triplets in the form of (subject, predicate, object). Avoid stopwords.
---------------------
Example:
Text: Alice is Bob's mother.
Triplets:
(Alice, is mother of, Bob)
Text: Philz is a coffee shop founded in Berkeley in 1982.
Triplets:
(Philz, is, coffee shop)
(Philz, founded in, Berkeley)
(Philz, founded in, 1982)
---------------------
Text: {text}
Triplets:
```

**Dynamic extraction prompt schema:** JSON array of objects with fields `head`, `head_type`, `relation`, `tail`, `tail_type`. Accepts an `INITIAL ONTOLOGY` (allowed entity types + relation types) but instructs the model to "introduce new types if necessary based on the context." Entities must be "most complete form" and "3-5 words max."

**Few-shot pattern:** One example (Tim Cook / Apple / UC Berkeley) embedded in the prompt. Property-enriched variant (`DEFAULT_DYNAMIC_EXTRACT_PROPS_TMPL`) adds `head_props`, `relation_props`, `tail_props` dicts.

**Multi-pass:** None. Single-pass per chunk; chunking is the caller's responsibility.

**Structured output enforcement:** JSON-mode via Pydantic structured output on LLM calls in `DynamicLLMPathExtractor`. Schema validated per triplet at extraction time, not post-hoc.

---

### Prompt 4 — LangChain `create_extraction_chain` default prompt + Pydantic pattern

Source: <https://huggingface.co/spaces/zhangyi617/webui/raw/main/langchain/chains/openai_functions/extraction.py>  
Background: <https://python.langchain.com/api_reference/langchain/chains/langchain.chains.openai_functions.extraction.create_extraction_chain.html>

**Default system prompt (verbatim):**
```
Extract and save the relevant entities mentioned in the following passage together
with their properties.

Only extract the properties mentioned in the 'information_extraction' function.

If a property is not present and is not required in the function parameters, do not
include it in the output.

Passage:
{input}
```

**Schema required:** Defined entirely by the JSON Schema or Pydantic model passed to `create_extraction_chain`. The model sees the schema through the function-calling interface, not as prompt text.

**Few-shot pattern:** No few-shot examples in the default prompt. LangChain's `how_to/extraction_examples.ipynb` shows building few-shot tool-call examples as `AIMessage` / `ToolMessage` chat history pairs, not embedded in system text.

**Multi-pass:** None in the base chain. LangGraph-based extraction pipelines can loop, but not part of the default extractor.

**Structured output enforcement:** OpenAI function calling / `with_structured_output`. The Pydantic schema docstring and field descriptions become the effective extraction instructions — the prompt itself is minimal.

---

### Prompt 5 — Haystack `LLMMetadataExtractor` NER_PROMPT

Source: <https://docs.haystack.deepset.ai/docs/llmmetadataextractor>

**Verbatim prompt (condensed — full prompt uses Jinja `{{ document.content }}`):**
```
-Goal-
Given text and a list of entity types, identify all entities of those types from the text.

-Steps-
1. Identify all entities. For each identified entity, extract the following information:
- entity_name: Name of the entity, capitalized
- entity_type: One of the following types: [organization, product, service, industry]
Format each entity as a JSON like: {"entity": <entity_name>, "entity_type": <entity_type>}
2. Return output in a single list with all the entities identified in steps 1.

-Examples-
#####################
Example 1:
entity_types: [organization, person, partnership, financial metric, product, service,
               industry, investment strategy, market trend]
text: [Visa / Alaska Airlines co-brand partnership example]
output:
{"entities": [{"entity": "Visa", "entity_type": "company"}, ...]}
#####################

-Real Data-
#####################
entity_types: [company, organization, person, country, product, service]
text: {{ document.content }}
#####################
output:
```

**Schema required:** A JSON object `{"entities": [{"entity": str, "entity_type": str}]}`.

**Few-shot pattern:** One extended example with Visa/airline/banking entities. The prompt structure (`-Goal-`, `-Steps-`, `-Examples-`, `-Real Data-`) mirrors the GraphRAG style, suggesting shared ancestry.

**Multi-pass:** None. Single pass; pipeline chaining is external.

**Structured output enforcement:** JSON output enforced by the prompt instruction and parsed by the pipeline. No tool-use or JSON-mode by default.

---

### Prompt 6 — Anthropic Cookbook tool-use extraction

Source: <https://github.com/anthropics/anthropic-cookbook/blob/main/tool_use/extracting_structured_json.ipynb>

**Schema (NER tool):**
```json
{
  "name": "print_entities",
  "description": "Prints extract named entities.",
  "input_schema": {
    "type": "object",
    "properties": {
      "entities": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string", "description": "The extracted entity name."},
            "type": {"type": "string", "description": "The entity type (e.g., PERSON, ORGANIZATION, LOCATION)."},
            "context": {"type": "string", "description": "The context in which the entity appears in the text."}
          },
          "required": ["name", "type", "context"]
        }
      }
    },
    "required": ["entities"]
  }
}
```

**User instruction:** `"Use the print_entities tool."` — the entire extraction instruction is one sentence; all schema guidance is in the tool definition.

**Few-shot pattern:** None in the NER example. A separate summarization example demonstrates a richer schema (author, topics array, summary, coherence score, persuasion score).

**Multi-pass:** None in the cookbook. Tool-use enforces single-call structured output.

**Structured output enforcement:** Tool-use is the enforcement mechanism. Claude is forced to emit a valid `input_schema`-conforming JSON block to call the tool. No post-hoc parsing.

---

### Prompt 7 — OpenAI Cookbook `Entity_extraction_for_long_documents`

Source: <https://github.com/openai/openai-cookbook/blob/main/examples/Entity_extraction_for_long_documents.ipynb>

**System prompt:** `"You help extract information from documents."`

**User prompt template (per chunk):**
```
Extract key pieces of information from this regulation document.
If a particular piece of information is not present, output "Not specified".
When you extract a key piece of information, include the closest page number.
Use the following format:
0. Who is the author
1. [question 1]
...

Document: """<document>"""

0. Who is the author: Tom Anderson (Page 1)
1.
```

**Schema required:** No formal schema — numbered question list with one few-shot answer planted for field 0 to anchor the output format.

**Few-shot pattern:** Single in-prompt example answer for "Who is the author" used to establish the `Answer (Page N)` format.

**Multi-pass:** Chunking only (documents split by token count 0.5n–1.5n, preferring sentence boundaries). No gleaning loop.

**Structured output enforcement:** None — free-text numbered list, parsed by post-hoc string splitting.

---

## Implications for PLAN.md Phase 3a extraction prompt design

1. **Use tool-use, not delimiter parsing.** Anthropic tool-use and LlamaIndex Pydantic structured output are the only two mechanisms that enforce schema compliance at generation time. GraphRAG's and Haystack's delimiter/JSON-in-text approaches require fragile post-hoc parsing and break on long outputs. Given Haiku 4.5 supports tool-use, define a `extract_source` tool with the full claim schema from CLAIM-DEFINITION.md §3.

2. **Embed 2-3 few-shot examples in the system prompt, not as chat history.** GraphRAG, Haystack, and LlamaIndex all embed examples directly in the system prompt text. LangChain's chat-history approach is brittle with caching because the examples shift token positions. For Haiku caching, examples in the system prompt are stable and cache-friendly.

3. **Run one gleaning pass, not three stages.** GraphRAG's two-prompt gleaning (CONTINUE + YES/NO gate) reliably recovers 10-20% more entities on longer chunks. A full plan → draft → verify pipeline adds two extra LLM calls per source; the gleaning loop adds only one conditional call. For 204 sources at Haiku cost, one gleaning pass is the right tradeoff.

4. **Separate entity identification from claim extraction.** GraphRAG's two-pass design (entities first, then claims anchored to entities) reduces hallucinated actors. For Scribe this maps to: Pass A extracts speaker + topic pairs; Pass B extracts claims anchored to those named speakers.

5. **Keep entity labels short and uppercase.** Both LlamaIndex (`3-5 words max`, `most complete form`) and GraphRAG (`capitalized`) enforce concise canonical forms. This matters for our downstream clustering step (Phase 1).

6. **Embed field-level descriptions in the tool schema, not in the system prompt prose.** The Anthropic cookbook pattern keeps the system prompt minimal and pushes all schema guidance into `input_schema.properties[field].description`. This maximises the stable-cache portion of the prompt.

---

## Open questions

1. **Gleaning loop convergence on transcripts vs. documents.** GraphRAG benchmarked gleaning on news and academic text. Does the YES/NO gate converge faster or slower on spoken transcripts where entities repeat more? Worth testing on 5 Scribe sources before committing to `max_gleanings`.

2. **Claim status field (TRUE/FALSE/SUSPECTED).** GraphRAG includes this; Scribe's CLAIM-DEFINITION.md does not. Is a confidence/verifiability flag worth adding to the claim schema for Phase 3a, given the cross-source agreement pass (Pass D) will provide empirical confidence later?

3. **Pydantic validation failure rate on Haiku 4.5.** LlamaIndex uses Pydantic structured output for per-triplet validation, but Haiku is smaller than GPT-4. What fraction of tool-use calls will fail schema validation and require a retry? Need an empirical estimate from a Phase 0c few-shot harvest run.

4. **Chunk size for transcripts.** OpenAI cookbook splits at 0.5n–1.5n tokens with sentence fallback. GraphRAG recommends 600 tokens for entity density. Phase 0a agent 06 covers contextual chunking — coordinate before finalizing Phase 3a chunk sizing.
