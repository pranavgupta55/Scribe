#!/usr/bin/env python3
"""Build graph/graph_v2.json from the v2 knowledge sources:
  - knowledge/concepts.json          (369 L0 concepts)
  - knowledge/v2/nodes.jsonl         (3270 L1/L2/L3/L4 nodes)
  - knowledge/v2/connections.json    (9464 sibling edges)
  - knowledge/sources.json           (204 source files, kept as source spokes)

Node schema:
  - id, label, level (L0|L1|L2|L2a|L2b|L3|L4'|source), plugin (None for L0/L1)
  - description (markdown)
  - n_sources (multi-source confirmation strength, for L2 nodes only)
  - parent_id (for hierarchy)

Edge schema:
  - source, target, weight, kind, confidence, sentence
"""
import json
import re
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
OUT_DIR = ROOT / "graph"
OUT_DIR.mkdir(exist_ok=True)
OUT = OUT_DIR / "graph_v2.json"

# Edge weight by kind (matches export_graph.py v1 conventions)
KIND_WEIGHT = {
    "agreement": 0.95,
    "builds-on": 0.80,
    "contradiction": 1.00,
    "related": 0.60,
}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def load_concepts():
    """L0 concepts."""
    data = json.loads((ROOT / "knowledge/concepts.json").read_text())
    out = {}
    for c in data["concepts"]:
        out[c["concept_id"]] = c
    return out


def load_v2_nodes():
    nodes = []
    with (ROOT / "knowledge/v2/nodes.jsonl").open() as f:
        for line in f:
            try:
                nodes.append(json.loads(line))
            except Exception:
                pass
    return nodes


def load_connections():
    return json.loads((ROOT / "knowledge/v2/connections.json").read_text())


def fmt_attribution(att_list, primary):
    """Render the attribution list as markdown lines."""
    if not att_list:
        return f"- **Speaker:** {primary or 'unknown'}"
    lines = [f"- **Speakers across {len(att_list)} source(s):**"]
    for att in att_list[:8]:
        s = att.get("source_file", "?").replace(".txt", "").replace("_", " ")
        spk = att.get("speaker") or "?"
        st = att.get("speaker_term")
        st_clause = f" (\"{st}\")" if st else ""
        lines.append(f"  - {spk}{st_clause} — *{s[:60]}*")
    if len(att_list) > 8:
        lines.append(f"  - … +{len(att_list)-8} more")
    return "\n".join(lines)


def main():
    concepts = load_concepts()
    v2_nodes = load_v2_nodes()
    conn = load_connections()
    sources_meta = json.loads((ROOT / "knowledge/sources.json").read_text())

    print(f"L0 concepts: {len(concepts)}")
    print(f"v2 nodes: {len(v2_nodes)}")
    print(f"sibling edges: {conn['n_edges']}")
    print(f"sources: {len(sources_meta)}")

    nodes = []
    edges = []

    # 1. L0 Concept nodes
    concept_node_id = {}
    for cid, c in concepts.items():
        node_id = f"concept:{c['canonical_name']}"
        concept_node_id[cid] = node_id
        desc = f"**{c['canonical_name']}** — {c['definition']}"
        if c.get("aliases"):
            desc += f"\n\n**Aliases:** {', '.join(c['aliases'][:8])}"
        if c.get("avoid_terms"):
            desc += f"\n\n**Avoid:** {', '.join(c['avoid_terms'])}"
        if c.get("ambiguity_notes"):
            desc += f"\n\n_Note: {c['ambiguity_notes']}_"
        nodes.append({
            "id": node_id,
            "label": c["canonical_name"],
            "level": "L0",
            "role": c.get("role", "domain"),
            "plugin": None,
            "description": desc,
            "n_members": len(c.get("members", [])),
        })

    # 2. L1/L2/L3/L4' nodes from v2/nodes.jsonl
    topic_to_concept_id = {}
    for cid, c in concepts.items():
        # Map every alias + canonical name → concept_id
        topic_to_concept_id[c["canonical_name"].lower()] = cid
        for alias in c.get("aliases", []):
            topic_to_concept_id[alias.lower()] = cid

    # Phase 4 indexed claims by their POSITION in the L2-filtered iteration of
    # nodes.jsonl (not by centroid_claim_id).  Rebuild that mapping.
    claim_position_to_node_id = {}
    position = 0
    for n in v2_nodes:
        nid = n.get("node_id", "")
        lvl = n.get("level", "")
        if nid.startswith("claim:") and lvl.startswith("L2"):
            claim_position_to_node_id[position] = f"claim:{nid.split(':',1)[1]}"
            position += 1

    claim_node_id_by_claim_id = {}
    for n in v2_nodes:
        level = n.get("level", "L2")
        nid_raw = n.get("node_id", "")
        # Construct stable global id
        if nid_raw.startswith("claim:"):
            node_id = f"claim:{nid_raw.split(':',1)[1]}"
            claim_node_id_by_claim_id[int(nid_raw.split(':',1)[1])] = node_id
        elif nid_raw.startswith("example:"):
            node_id = f"example:{nid_raw.split(':',1)[1]}"
        elif nid_raw.startswith("practice:"):
            node_id = f"practice:{nid_raw.split(':',1)[1]}"
        elif nid_raw.startswith("framework:"):
            node_id = f"framework:{nid_raw.split(':',1)[1]}"
        else:
            node_id = nid_raw

        topic = n.get("topic", "") or n.get("concept", "")
        parent_concept_id = topic_to_concept_id.get(topic.lower())

        # Build description per level
        if level == "L1":
            label = n.get("name", "(framework)")
            desc = f"**Framework: {label}**"
            if n.get("definition"):
                desc += f" — {n['definition']}"
        elif level in ("L2", "L2a", "L2b"):
            label = (n.get("text", "")[:80] + "…") if len(n.get("text", "")) > 80 else n.get("text", "")
            text = n.get("text", "")
            desc = text
            if n.get("conditions"):
                desc += f"\n\n**Conditions:** {', '.join(n['conditions'][:5])}"
            if n.get("mechanism"):
                desc += f"\n\n**Mechanism:** {n['mechanism']}"
            if n.get("numbers"):
                num = n["numbers"]
                if isinstance(num, dict):
                    axis = num.get("axis", "")
                    anchors = num.get("anchors", [])
                    if axis:
                        desc += f"\n\n**Quantified Axis ({axis}):**"
                        for a in anchors[:6]:
                            if isinstance(a, dict):
                                desc += f"\n  - {a.get('region','')}: {a.get('value','')}"
                            else:
                                desc += f"\n  - {a}"
            if n.get("bounded_by"):
                desc += f"\n\n**Counter-bounded by:** {', '.join(n['bounded_by'][:3])}"
            desc += f"\n\n{fmt_attribution(n.get('attribution_list', []), n.get('primary_speaker'))}"
            if n.get("n_sources", 1) >= 2:
                desc += f"\n\n✨ **Cross-source confirmed by {n['n_sources']} sources.**"
        elif level == "L3":
            text = n.get("text", "")
            label = (text[:80] + "…") if len(text) > 80 else text
            desc = f"**Example:** {text}"
            if n.get("source_file"):
                desc += f"\n\n_from {n['source_file']}_"
        elif level == "L4'":
            text = n.get("text", "")
            label = (text[:80] + "…") if len(text) > 80 else text
            desc = f"**Practice:** {text}"
            if n.get("source_file"):
                desc += f"\n\n_from {n['source_file']}_"
        else:
            label = nid_raw
            desc = json.dumps(n)[:300]

        nodes.append({
            "id": node_id,
            "label": label or node_id,
            "level": level,
            "plugin": topic,           # for parent grouping in viewer
            "description": desc,
            "n_sources": n.get("n_sources", 1),
            "parent_id": concept_node_id.get(parent_concept_id) if parent_concept_id is not None else None,
            "topic": topic,
        })

        # Parent-child edge to L0 concept
        if parent_concept_id is not None:
            edges.append({
                "source": concept_node_id[parent_concept_id],
                "target": node_id,
                "kind": "hosts" if level in ("L2", "L2a", "L2b", "L1") else "illustrates" if level == "L3" else "practices" if level == "L4'" else "child",
                "weight": 0.5,
                "confidence": 1.0,
                "sentence": "",
            })

    # 3. Source nodes (book/video transcripts)
    for src_name, meta in sources_meta.items():
        node_id = f"source:{src_name}"
        title = meta.get("title", src_name)
        summary = meta.get("video_summary", "")
        url = meta.get("url", "")
        desc = f"**{title}**"
        if summary:
            desc += f"\n\n{summary}"
        if url:
            desc += f"\n\n[Open source]({url})"
        nodes.append({
            "id": node_id,
            "label": title[:60] if title else src_name,
            "level": "source",
            "plugin": None,
            "description": desc,
            "url": url,
        })

    # 4. Sibling edges from connections.json
    n_concept_match = 0
    n_claim_match = 0
    n_skip = 0
    concept_id_by_int = {cid: nid for cid, nid in concept_node_id.items()}
    for e in conn["edges"]:
        if e["pair_kind"] == "concept":
            a_node = concept_id_by_int.get(e["a"])
            b_node = concept_id_by_int.get(e["b"])
            if a_node and b_node:
                edges.append({
                    "source": a_node,
                    "target": b_node,
                    "kind": e["edge_kind"],
                    "weight": KIND_WEIGHT.get(e["edge_kind"], 0.5),
                    "confidence": e["confidence"],
                    "sentence": e["sentence"],
                })
                n_concept_match += 1
            else:
                n_skip += 1
        else:  # claim pair — Phase 4 used POSITION not claim_id
            a_node = claim_position_to_node_id.get(e["a"])
            b_node = claim_position_to_node_id.get(e["b"])
            if a_node and b_node:
                edges.append({
                    "source": a_node,
                    "target": b_node,
                    "kind": e["edge_kind"],
                    "weight": KIND_WEIGHT.get(e["edge_kind"], 0.5),
                    "confidence": e["confidence"],
                    "sentence": e["sentence"],
                })
                n_claim_match += 1
            else:
                n_skip += 1

    print(f"sibling edges resolved: concept={n_concept_match}, claim={n_claim_match}, skipped={n_skip}")
    print(f"\ntotal nodes: {len(nodes)}")
    print(f"total edges: {len(edges)}")

    # Level distribution
    level_dist = Counter(n["level"] for n in nodes)
    print(f"\nlevel distribution: {dict(level_dist)}")
    edge_kind_dist = Counter(e["kind"] for e in edges)
    print(f"edge kind distribution: {dict(edge_kind_dist)}")

    OUT.write_text(json.dumps({"nodes": nodes, "links": edges}, indent=1))
    print(f"\nwrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
