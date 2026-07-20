#!/usr/bin/env python3
"""Side-by-side eval: v1 vs v2 retrieval on the same query set.

Hits POST /api/chat (v1 flow) and POST /api/chat with use_v2=true against
a running server.py. Streams SSE events. For each query, captures:

  - v1: consulted topics, source count, first ~200 chars of answer
  - v2: retrieval meta stats, prompt stats, first ~200 chars of answer

Writes .forge_scratch/scribe_rag_v2/eval_v1_v2.json with the pairs.

Usage:
  python3 scripts/eval_v1_vs_v2.py [--server http://localhost:8765]
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
OUT_PATH = REPO_ROOT / ".forge_scratch" / "scribe_rag_v2" / "eval_v1_v2.json"

# A tight set of queries touching different Hormozi/Ravikant/Ericvelch themes.
# Some are single-topic (should test raw retrieval), some are multi-topic
# (should test decomposition), some are opinion-y (should surface contradictions).
QUERIES = [
    "How does Hormozi think about founder focus and attention?",
    "What's the difference between building a business and building an audience?",
    "When should I raise prices vs add a guarantee?",
    "What do successful entrepreneurs say about mentorship?",
    "Compare hiring virtual assistants to hiring local employees.",
    "Is drop-servicing a real business or just arbitrage?",
]


def _post_chat(server: str, query: str, use_v2: bool, timeout: int = 90) -> dict:
    """POST /api/chat and consume the SSE stream. Returns collected events."""
    payload = json.dumps({"query": query, "use_v2": use_v2}).encode()
    req = urllib.request.Request(
        f"{server}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    events = {"nodes": None, "sources": None, "debug": None, "backend": None,
              "tokens": [], "error": None, "notice": None, "done": False}

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            for chunk in resp:
                buf += chunk
                while b"\n\n" in buf:
                    frame, buf = buf.split(b"\n\n", 1)
                    for line in frame.split(b"\n"):
                        if not line.startswith(b"data: "):
                            continue
                        try:
                            evt = json.loads(line[6:].decode())
                        except json.JSONDecodeError:
                            continue
                        t = evt.get("type")
                        if t == "nodes":
                            events["nodes"] = evt.get("nodes")
                        elif t == "sources":
                            events["sources"] = evt.get("sources")
                        elif t == "debug":
                            events["debug"] = {
                                "sub_queries": evt.get("sub_queries"),
                                "system_tokens": evt.get("system_tokens"),
                                "history_tokens": evt.get("history_tokens"),
                                "v2": evt.get("v2"),
                            }
                        elif t == "backend":
                            events["backend"] = evt.get("backend")
                        elif t == "token":
                            events["tokens"].append(evt.get("text", ""))
                        elif t == "notice":
                            events["notice"] = evt.get("text")
                        elif t == "error":
                            events["error"] = evt.get("message")
                        elif t == "done":
                            events["done"] = True
                            break
                    if events["done"]:
                        break
                if events["done"]:
                    break
    except (urllib.error.URLError, TimeoutError) as e:
        events["error"] = str(e)

    events["wall_ms"] = int((time.time() - t0) * 1000)
    events["answer_preview"] = "".join(events["tokens"])[:400]
    events["answer_length_chars"] = sum(len(t) for t in events["tokens"])
    events["n_tokens_streamed"] = len(events["tokens"])
    del events["tokens"]  # save space in output
    return events


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default="http://localhost:8765")
    p.add_argument("--queries", type=str, default="",
                   help="path to a text file with one query per line (default: built-in)")
    args = p.parse_args()

    queries = QUERIES
    if args.queries:
        queries = [q.strip() for q in Path(args.queries).read_text().splitlines() if q.strip()]

    results = []
    for i, q in enumerate(queries):
        print(f"\n[{i+1}/{len(queries)}] {q}")

        print("  v1 ...", end=" ", flush=True)
        v1 = _post_chat(args.server, q, use_v2=False)
        print(f"{v1['wall_ms']}ms · {v1['n_tokens_streamed']}toks · {'OK' if not v1['error'] else v1['error']}")

        print("  v2 ...", end=" ", flush=True)
        v2 = _post_chat(args.server, q, use_v2=True)
        print(f"{v2['wall_ms']}ms · {v2['n_tokens_streamed']}toks · {'OK' if not v2['error'] else v2['error']}")

        if v2.get("debug", {}).get("v2"):
            meta = v2["debug"]["v2"].get("retrieval_meta", {})
            stats = v2["debug"]["v2"].get("prompt_stats", {})
            print(f"    v2 stats: {stats.get('n_facts', '?')} facts, "
                  f"{stats.get('n_chunks', '?')} chunks, "
                  f"{stats.get('n_contradictions', '?')} contradictions, "
                  f"{stats.get('total_chars', '?')} prompt chars")

        results.append({"query": q, "v1": v1, "v2": v2})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
