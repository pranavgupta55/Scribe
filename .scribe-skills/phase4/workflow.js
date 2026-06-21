// Phase 4 — connection judging. One Haiku agent per batch (40 batches × ~364 pairs).
// For each candidate pair (concept↔concept or claim↔claim), assign edge_kind:
//   agreement / builds-on / contradiction / related / none

export const meta = {
  name: 'phase4-judge',
  description: 'Haiku connection judges on ~14k candidate pairs across 40 batches',
  phases: [
    { title: 'Judge', detail: '40 Haiku batches × ~364 pairs each' },
  ],
}

function buildPrompt(batchPath) {
  return `Phase 4 connection-judge agent (Haiku 4.5).

READ:
1. ${batchPath}  (YOUR BATCH of ~364 candidate pairs)

TASK
For each pair in batch.pairs, decide edge_kind:
- "agreement"     — both make the same claim, possibly with different framing (mark "high" confidence if textually near-identical)
- "builds-on"     — one extends, refines, or specializes the other; reading both adds value beyond either alone
- "contradiction" — they assert mutually-exclusive positions on the same axis
- "related"       — same topic / adjacent concept but no direct logical link
- "none"          — coincidental similarity; not worth showing as an edge

Each pair has kind="concept" (concept↔concept) or kind="claim" (claim↔claim). Same rubric for both.

For concept pairs: compare a_def + a_name vs b_def + b_name.
For claim pairs: compare a_claim_text vs b_claim_text. Consider speaker, topic, conditions when present.

Be conservative on "agreement" and "contradiction" (specific). Be liberal on "related" / "none".
Aim for distribution roughly: agreement 10-20%, builds-on 15-25%, contradiction <5%, related 30-50%, none 20-40%.

WRITE result to:
/Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/phase4/batches/scored_NN.json
where NN = batch.batch_id zero-padded to 2 digits.

Format MUST be a JSON ARRAY:
[
  {
    "a_id": <int — a_concept_id for concept pairs, a_claim_id for claim pairs>,
    "b_id": <int — b_concept_id or b_claim_id>,
    "pair_kind": "concept"|"claim",
    "edge_kind": "agreement"|"builds-on"|"contradiction"|"related"|"none",
    "confidence": "high"|"medium"|"low",
    "sentence": "<one short sentence justifying the edge_kind, max 20 words>"
  },
  ...
]

VALIDATE JSON parses. Straight quotes only.

REPLY with ONE line:
\`done: batch_NN scored=<n> agreement=<n> builds-on=<n> contradiction=<n> related=<n> none=<n>\``
}

phase('Judge')

let cfg = args
if (typeof cfg === 'string') cfg = JSON.parse(cfg)
const batches = cfg.batches
if (!Array.isArray(batches)) throw new Error(`args.batches missing — got ${typeof batches}`)

log(`Phase 4 judging — ${batches.length} batches × Haiku`)

const results = await parallel(
  batches.map((batchPath) => () =>
    agent(buildPrompt(batchPath), {
      label: `judge:${batchPath.split('/').pop().replace('.json','')}`,
      model: 'haiku',
      phase: 'Judge',
    })
      .then((reply) => ({ batch: batchPath, reply: String(reply || '').slice(0, 200), ok: true }))
      .catch((e) => ({ batch: batchPath, error: String(e).slice(0, 200), ok: false }))
  )
)

const ok = results.filter((r) => r && r.ok)
const failed = results.filter((r) => r && !r.ok)
log(`Phase 4 complete — ${ok.length} ok, ${failed.length} failed`)

return {
  ok_count: ok.length,
  failed_count: failed.length,
  failed_batches: failed.map((r) => r.batch).slice(0, 20),
  sample_replies: ok.slice(0, 4).map((r) => r.reply),
}
