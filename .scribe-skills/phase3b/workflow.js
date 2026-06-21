// Phase 3b — hierarchy classification of merged claims.
// One Haiku agent per batch (12 batches × ~155 claims each).
// Each agent assigns each claim to L2 (assertion) / L2a (mechanism) / L2b (quantified) / DROP.

export const meta = {
  name: 'phase3b-classify',
  description: 'Assign hierarchy level (L2/L2a/L2b/DROP) to each merged claim',
  phases: [
    { title: 'Classify', detail: '12 Haiku batches × ~155 claims each' },
  ],
}

function buildPrompt(batchPath) {
  return `Phase 3b classification agent (Haiku 4.5).

READ in order:
1. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/HIERARCHY.md (§1 only — level definitions)
2. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/NODE-QUALITY-RUBRIC.md (§A failure modes — drop claims that fail A1, A3 (un-quantified numbers), A10 (rule-shaped missing conditions))
3. ${batchPath}  (YOUR BATCH)

TASK
For each claim in batch.claims, assign exactly ONE level:
- L2  — assertion (declarative claim, no because-clause)
- L2a — mechanism (causal: X because Y, or X via Y)
- L2b — quantified axis (claim contains numbers + axis with anchors)
- DROP — fails NODE-QUALITY-RUBRIC §A (un-named speaker, decontextualized number, vague rule)

Use the claim.type field as a STRONG hint:
  type=assertion → L2
  type=mechanism → L2a
  type=quantified → L2b
  type=conditional → L2 (unless mechanism present)
  type=comparison → L2 (unless quantified)
Override only when content disagrees with type.

DROP discipline: aim for ≤10% drop rate. Demote questionable claims to L2 (lowest tier) rather than dropping.

WRITE result to:
/Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/phase3b/classify_batches/scored_NN.json
where NN = batch.batch_id zero-padded to 2 digits.

Format MUST be a JSON ARRAY:
[
  {"claim_id": <int>, "level": "L2"|"L2a"|"L2b"|"DROP", "rationale": "<10 words>"},
  ...
]

VALIDATE JSON parses before writing. Straight quotes only.

REPLY with ONE line:
\`done: batch_NN classified=<n> L2=<n> L2a=<n> L2b=<n> DROP=<n>\``
}

phase('Classify')

let cfg = args
if (typeof cfg === 'string') cfg = JSON.parse(cfg)
const batches = cfg.batches
if (!Array.isArray(batches)) throw new Error(`args.batches missing or not array — got ${typeof batches}`)

log(`Phase 3b classifying — ${batches.length} batches × Haiku`)

const results = await parallel(
  batches.map((batchPath) => () =>
    agent(buildPrompt(batchPath), {
      label: `classify:${batchPath.split('/').pop().replace('.json','')}`,
      model: 'haiku',
      phase: 'Classify',
    })
      .then((reply) => ({ batch: batchPath, reply: String(reply || '').slice(0, 200), ok: true }))
      .catch((e) => ({ batch: batchPath, error: String(e).slice(0, 200), ok: false }))
  )
)

const ok = results.filter((r) => r && r.ok)
const failed = results.filter((r) => r && !r.ok)
log(`Phase 3b complete — ${ok.length} ok, ${failed.length} failed`)

return {
  ok_count: ok.length,
  failed_count: failed.length,
  failed_batches: failed.map((r) => r.batch).slice(0, 12),
  sample_replies: ok.slice(0, 4).map((r) => r.reply),
}
