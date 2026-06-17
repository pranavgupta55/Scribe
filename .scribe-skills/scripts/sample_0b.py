#!/usr/bin/env python3
"""Phase 0b — stratified diagnostic sampling.

Produces 12 batches of 20 nodes each (240 total) drawn from four pools:

  - 60 topic-note candidates (.md heads / sections from knowledge/topics/*.md)
  - 60 connection-edge candidates (knowledge/connections.json)
  - 60 video_summary candidates (knowledge/sources.json)
  - 60 multi-source topic-string candidates (the dedup-stress pool)

Each candidate is tagged with surface failure-mode signals (A1..A20 from
NODE-QUALITY-RUBRIC.md) so the agents can be reasoned about later.
Batches are written to .scribe-skills/research/0b-batches/batch_NN.json.

This is deliberately heuristic — the agents do the real labeling.
"""
import json
import random
import re
from pathlib import Path
from collections import defaultdict

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
OUT_DIR = ROOT / ".scribe-skills/research/0b-batches"
OUT_DIR.mkdir(parents=True, exist_ok=True)

random.seed(0xC0FFEE)


def surface_modes(text: str) -> list[str]:
    """Cheap regex heuristics — does this text look like it might trip a rubric
    failure mode? Used only to stratify the sample, not to label."""
    t = text or ""
    modes = []
    if re.search(r"\b(the )?(founder|CEO|owner|the team|the buyer|the customer)\b", t, re.I):
        modes.append("A1")
    if re.search(r"(offer|provide|include) [a-z\-]+, [a-z\-]+, [a-z\-]+", t, re.I):
        modes.append("A2")
    if re.search(r"\b\d{1,4}(\.\d+)?\b", t) and not re.search(r"axis|range|stage|level|tier", t, re.I):
        modes.append("A3")
    if re.search(r"\b(in the middle|moderate|balanced|reasonable|appropriate)\b", t, re.I):
        modes.append("A4")
    if t.count("\n- ") >= 3 and all(len(b.strip()) < 60 for b in t.split("\n- ")[1:4]):
        modes.append("A5_or_A6")
    if re.search(r"\b(always|never) (close|do|hire|fire)\b", t, re.I):
        modes.append("A8_or_A10")
    if re.search(r"\b(better|more effective|higher quality|superior)\b", t, re.I) and not re.search(r"\b\d", t):
        modes.append("A18")
    return modes


def load_topic_notes() -> list[dict]:
    out = []
    for p in sorted((ROOT / "knowledge/topics").glob("*.md"))[:1472]:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        # First the headline (the `> ...` line) then a sample of body
        m = re.search(r"^>\s*(.+)$", text, re.M)
        head = m.group(1).strip() if m else ""
        body_sample = "\n".join(text.splitlines()[:40])
        out.append({
            "node_id": f"topic:{p.stem}",
            "node_kind": "topic_note",
            "source": p.relative_to(ROOT).as_posix(),
            "headline": head,
            "text": body_sample[:1800],
            "surface_modes": surface_modes(body_sample),
        })
    return out


def load_edges() -> list[dict]:
    conn = json.loads((ROOT / "knowledge/connections.json").read_text())
    out = []
    for e in conn.get("edges", []):
        text = e.get("sentence", "")
        out.append({
            "node_id": f"edge:{e.get('a','?')}|{e.get('b','?')}",
            "node_kind": "edge",
            "source": "knowledge/connections.json",
            "a": e.get("a"),
            "b": e.get("b"),
            "kind": e.get("kind"),
            "cross_source": e.get("cross_source"),
            "text": text,
            "surface_modes": surface_modes(text),
        })
    return out


def load_video_summaries() -> list[dict]:
    src = json.loads((ROOT / "knowledge/sources.json").read_text())
    out = []
    for name, meta in src.items():
        summ = meta.get("video_summary", "")
        if not summ:
            continue
        out.append({
            "node_id": f"summary:{name}",
            "node_kind": "video_summary",
            "source": f"knowledge/sources.json::{name}",
            "title": meta.get("title", ""),
            "text": summ,
            "surface_modes": surface_modes(summ),
        })
    return out


def load_multi_source_topics() -> list[dict]:
    """The dedup-stress pool: topic strings that appear in 2+ sources, plus a
    sampling of their near-duplicate single-source siblings (orphan_candidates
    if present, else a random subset)."""
    src = json.loads((ROOT / "knowledge/sources.json").read_text())
    freq = defaultdict(list)
    for sname, meta in src.items():
        for t in meta.get("topics", []):
            freq[t].append(sname)

    multi = [(t, srcs) for t, srcs in freq.items() if len(srcs) >= 2]
    single = [(t, srcs) for t, srcs in freq.items() if len(srcs) == 1]
    random.shuffle(single)
    pool = multi + single[: max(0, 60 - len(multi))]

    out = []
    for t, srcs in pool[:60]:
        out.append({
            "node_id": f"topic_str:{t}",
            "node_kind": "topic_string_cluster_seed",
            "source": "knowledge/sources.json (topic dedup)",
            "topic_string": t,
            "sources": srcs[:5],
            "n_sources": len(srcs),
            "text": f"Topic string '{t}' appears in {len(srcs)} source(s).",
            "surface_modes": [],
        })
    return out


def stratified_pick(pool, n, key=lambda x: tuple(sorted(x.get("surface_modes", []))), seed_label=""):
    """Pick n items maximizing failure-mode coverage. Greedy: while remaining
    < n, pick whichever bucket has the rarest mode-signature seen so far."""
    by_sig = defaultdict(list)
    for x in pool:
        by_sig[key(x)].append(x)
    for bucket in by_sig.values():
        random.shuffle(bucket)
    picked = []
    sigs = list(by_sig.keys())
    while len(picked) < n and any(by_sig.values()):
        # round-robin across signatures so we cover failure modes
        for s in sigs:
            if by_sig[s]:
                picked.append(by_sig[s].pop())
                if len(picked) >= n:
                    break
    return picked


def main():
    topic_notes = load_topic_notes()
    edges = load_edges()
    summaries = load_video_summaries()
    multi_topics = load_multi_source_topics()

    print(f"pools: notes={len(topic_notes)} edges={len(edges)} "
          f"summaries={len(summaries)} multi_topics={len(multi_topics)}")

    pick_notes = stratified_pick(topic_notes, 60)
    pick_edges = stratified_pick(edges, 60)
    pick_summ  = stratified_pick(summaries, 60)
    pick_multi = stratified_pick(multi_topics, 60)

    all_nodes = pick_notes + pick_edges + pick_summ + pick_multi
    random.shuffle(all_nodes)
    assert len(all_nodes) == 240, f"expected 240 got {len(all_nodes)}"

    for i in range(12):
        batch = all_nodes[i * 20:(i + 1) * 20]
        out = OUT_DIR / f"batch_{i+1:02d}.json"
        out.write_text(json.dumps({"batch_id": i + 1, "nodes": batch}, indent=2))
        print(f"wrote {out.relative_to(ROOT)}  ({len(batch)} nodes)")


if __name__ == "__main__":
    main()
