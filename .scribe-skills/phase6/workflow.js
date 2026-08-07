// Phase 6 (Wave 3) — graph quality eval.
// 16 Haiku scoring agents (200 claims) + 4 Sonnet auditor agents (40-claim second opinion).
// Each agent reads its batch, applies NODE-QUALITY-RUBRIC Section C (7-row matrix) to every
// claim, and writes a scored file. Aggregation → pass rate is done by a separate python script.

export const meta = {
  name: 'phase6-quality-eval',
  description: 'Haiku node-quality scorers + Sonnet auditors on Wave 3 graph_v2 claims',
  phases: [
    { title: 'Score', detail: '16 Haiku batches × ~13 claims (rubric §C)' },
    { title: 'Audit', detail: '4 Sonnet auditors × 10 claims (independent second opinion)' },
  ],
}

const DIR = '/Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/phase6/wave3'

const RUBRIC = `NODE-QUALITY-RUBRIC — Section C scoring matrix. For each claim answer Y/N per row:
1. Resolvable referents — every actor/company/product/time period is a named identity, not a generic role-word ("the founder", "larger companies").
2. Standalone meaning — reads correctly out of context, without a preceding bullet.
3. Load-bearing — if it were false, a reader's behavior or model would change (not a truism/tautology).
4. Right type label — the node's role is correctly declared (assertion/Claim, mechanism/Mechanism, Anecdote, Axis, Metaphor, Counterexample, Definition, Contradiction).
5. Falsifiable — some evidence or comparison would settle whether it is true.
6. Axis-anchored (numbers only) — any number carries its axis + at least one comparison point.
7. Source-grounded — attribution (speaker + source) is present and lookupable.

REQUIRED ROWS BY TYPE:
- A claim/assertion/mechanism MUST score Y on ALL of rows 1-5 (and 7).
- An example/anecdote MUST score Y on rows 1, 2, 4 (and 7).
- A number/quantified-axis (level L2b) MUST score Y on rows 1, 2, 6 (and 7).
- Row 7 (source-grounded) is required for EVERY node.

Judge the claim's own required rows only. \`passing\` = Y on all required rows for that type.

Common failure modes to tag in failure_modes[] when a row fails (use these codes):
A1 ambiguous-actor, A3 decontextualized-number, A4 vague-blanket, A9 tautology, A10 hidden-conditional,
A11 attribution-missing, A13 universalized-anecdote, A15 correlation-as-causation, A16 mixed-grain,
A18 vague-comparative, A20 bogus-precision.`

function buildPrompt(batchPath, outName, tier) {
  return `${tier === 'sonnet' ? 'Phase 6 SONNET AUDITOR' : 'Phase 6 Haiku scorer'} (graph quality eval).

${RUBRIC}

READ your batch: ${batchPath}
It is JSON: { batch_id, claims: [ { node_id, level, topic, text, type, speaker, speaker_term, conditions[], mechanism, numbers, bounded_by[], n_sources } ] }.

Level → required-row mapping: L2 and L2a are claims (rows 1-5 + 7). L2b is a quantified axis (rows 1,2,6 + 7).
Score STRICTLY and independently — do not assume a claim passes because it is well-written; check each required row against the actual text, conditions, mechanism, and attribution.
${tier === 'sonnet' ? 'You are the second opinion: be adversarial. Look for false-passes the first-pass scorer would miss (subtle A1 ambiguous actors, A15 correlation-as-causation, A13 over-generalized anecdotes).' : ''}

WRITE result to: ${DIR}/${outName}
Format MUST be a JSON ARRAY, one object per claim, IN INPUT ORDER:
[
  {
    "node_id": "claim:NNNN",
    "level": "L2|L2a|L2b",
    "type": "<the claim's type>",
    "scores": { "1": true, "2": true, "3": true, "4": true, "5": true, "6": null, "7": true },
    "passing": true,
    "failure_modes": [],
    "notes": "<one terse sentence: why it passed, or which required row failed and why>"
  }
]
Use null for rows that do not apply to this claim's type. \`passing\` reflects only the required rows for the type.
VALIDATE the JSON parses. Straight quotes only. Score EVERY claim in the batch.

REPLY with ONE line:
\`done: ${outName} scored=<n> passing=<n> failing=<n>\``
}

let cfg = args
if (typeof cfg === 'string') cfg = JSON.parse(cfg)
const haiku  = cfg.haiku_batches
const sonnet = cfg.sonnet_batches || []
if (!Array.isArray(haiku)) throw new Error(`args.haiku_batches missing — got ${typeof haiku}`)

log(`Phase 6 eval — ${haiku.length} Haiku scoring batches + ${sonnet.length} Sonnet auditors`)

// Both phases are independent — run them concurrently.
const scoreThunks = haiku.map((bp) => () => {
  const nn = bp.split('/').pop().replace('batch_', '').replace('.json', '')
  return agent(buildPrompt(bp, `scored_${nn}.json`, 'haiku'), {
    label: `score:${nn}`, model: 'haiku', phase: 'Score',
  })
    .then((r) => ({ batch: bp, reply: String(r || '').slice(0, 160), ok: true }))
    .catch((e) => ({ batch: bp, error: String(e).slice(0, 160), ok: false }))
})

const auditThunks = sonnet.map((bp) => () => {
  const nn = bp.split('/').pop().replace('audit_', '').replace('.json', '')
  return agent(buildPrompt(bp, `audited_${nn}.json`, 'sonnet'), {
    label: `audit:${nn}`, model: 'sonnet', phase: 'Audit',
  })
    .then((r) => ({ batch: bp, reply: String(r || '').slice(0, 160), ok: true }))
    .catch((e) => ({ batch: bp, error: String(e).slice(0, 160), ok: false }))
})

const all = await parallel([...scoreThunks, ...auditThunks])
const results = all.filter(Boolean)
const ok = results.filter((r) => r.ok)
const failed = results.filter((r) => !r.ok)
log(`Phase 6 complete — ${ok.length} ok, ${failed.length} failed`)

return {
  ok_count: ok.length,
  failed_count: failed.length,
  failed_batches: failed.map((r) => r.batch),
  sample_replies: ok.slice(0, 6).map((r) => r.reply),
}
