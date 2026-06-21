// Phase 3a — extraction. One Sonnet 4.6 agent per transcript.
// Each agent reads concepts_index + transcript and WRITES its extraction to
// .scribe-skills/phase3a/extracted/{name}.json. Agent returns a one-line summary.

export const meta = {
  name: 'phase3a-extraction',
  description: 'Two-pass claim+framework+example extraction from each transcript, anchored to canonical concepts',
  phases: [
    { title: 'Extract', detail: 'one Sonnet 4.6 per transcript (204 sources)' },
  ],
}

function buildExtractionPrompt(source) {
  const name = source.name
  const title = source.title || name.replace(/\.txt$/, '').replace(/_/g, ' ')
  const summary = source.video_summary || ''
  return `Phase 3a extraction agent (Sonnet 4.6) for source: ${name}

READ in order:
1. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/CLAIM-DEFINITION.md (§3 claim shape + §4 why we include)
2. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/NODE-QUALITY-RUBRIC.md (§A failure modes A1–A20)
3. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/FEW-SHOT.md (positive/negative canonical examples)
4. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/HIERARCHY.md (skim §1)
5. /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/phase3a/concepts_index.json (369 canonical L0 concepts)
6. /Users/pranavgupta/VSCode Projects/Scribe/transcripts/${name}  (YOUR TRANSCRIPT)

SOURCE METADATA
- file: ${name}
- title: ${title}
${summary ? `- video_summary: ${summary}\n` : ''}
TWO-PASS EXTRACTION (per 0a-research-02 synthesis):

PASS 1 — speakers + concepts.
Identify every named speaker in the transcript (primary host + any quoted/guest figures), and identify which L0 concepts from concepts_index.json this transcript addresses. Use concept canonical_name VERBATIM (or an alias from the same concept). Do NOT invent new concepts — choose closest existing match or skip claims for unmappable topics.

PASS 2 — claims + frameworks + examples + practices.
Extract content under the DECONTEXTUALIZATION gate (CLAIM-DEFINITION §3): a claim's text must allow a reader with no access to the source to understand the assertion AND identify what evidence would settle truth/falsity.

For EVERY claim:
- text: DECONTEXTUALIZED prose (names the speaker inline if attribution is load-bearing).
- type: assertion | mechanism | comparison | conditional | quantified.
- speaker: the named individual asserting the claim. Never "the founder", "the CEO" (A1).
- speaker_term: the surface form the speaker used (may be null if speaker used canonical name).
- topic: canonical_name from concepts_index.json VERBATIM (one and only one).
- conditions: [...] when rule-shaped (audience, price band, channel, stage). REQUIRED for rule-shaped claims (A10).
- mechanism: string (the because-clause) when causal; null otherwise.
- numbers: { axis, anchors[] } REQUIRED when claim contains numbers (A3); else null. Anchors are >=2 named points on the axis.
- bounded_by: [...] explicit counterexamples / non-applicability the speaker raises.

For FRAMEWORKS (named multi-part structures only):
- name, definition (1-line), concept (the L0 concept it belongs to).

For EXAMPLES (specific stories illustrating a claim):
- text (one coherent paragraph, not bullets per event — A6).
- claim_index (which claim in your claims[] it illustrates; -1 if standalone).
- topic.

For PRACTICES (bare imperatives without a backing claim — demote rather than dropping):
- text, topic.

DEMOTION DISCIPLINE: weak claim → example. Weak example → implementation detail (drop). Bare imperative → practice. Aim 10–30 high-quality claims per typical transcript; fewer for short ones.

WRITE the result to: /Users/pranavgupta/VSCode Projects/Scribe/.scribe-skills/phase3a/extracted/${name.replace(/\.txt$/, '.json')}

The file must be a JSON OBJECT with this exact shape:
{
  "source_file": "${name}",
  "primary_speaker": "<named individual>",
  "concepts_addressed": ["<canonical_name>", ...],
  "frameworks": [{"name": "...", "definition": "...", "concept": "..."}, ...],
  "claims": [
    {
      "text": "...",
      "type": "assertion|mechanism|comparison|conditional|quantified",
      "speaker": "...",
      "speaker_term": "..."|null,
      "topic": "...",
      "conditions": [...],
      "mechanism": "..."|null,
      "numbers": {"axis":"...", "anchors":[...]}|null,
      "bounded_by": [...]
    }
  ],
  "examples": [{"text":"...", "claim_index":<int>, "topic":"..."}],
  "practices": [{"text":"...", "topic":"..."}]
}

VALIDATE that your JSON parses before writing. Use straight quotes only. ESCAPE quotes inside strings.

REPLY with ONE line in the format:
\`done: ${name} claims=<n> frameworks=<n> examples=<n> practices=<n> concepts=<n>\``
}

phase('Extract')

// args may be the object literal or a JSON-encoded string of it
let cfg = args
if (typeof cfg === 'string') cfg = JSON.parse(cfg)
const sources = cfg && cfg.sources
const modelChoice = (cfg && cfg.model) || 'sonnet'
if (!sources) {
  throw new Error(`args.sources missing — typeof args=${typeof args}, keys=${args && Object.keys(args)}`)
}

log(`Phase 3a extraction starting — ${sources.length} sources, model=${modelChoice}`)

const results = await parallel(
  sources.map((s) => () =>
    agent(buildExtractionPrompt(s), {
      label: `extract:${s.name.replace(/\.txt$/, '').slice(0, 32)}`,
      model: modelChoice,
      phase: 'Extract',
    })
      .then((reply) => ({ name: s.name, reply: String(reply || '').slice(0, 300), ok: true }))
      .catch((e) => ({ name: s.name, error: String(e).slice(0, 200), ok: false }))
  )
)

const ok = results.filter((r) => r && r.ok)
const failed = results.filter((r) => r && !r.ok)
log(`Phase 3a complete — ${ok.length} ok, ${failed.length} failed`)

return {
  ok_count: ok.length,
  failed_count: failed.length,
  failed_names: failed.map((r) => r.name).slice(0, 20),
  sample_replies: ok.slice(0, 5).map((r) => r.reply),
}
