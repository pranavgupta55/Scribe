#!/usr/bin/env python3
"""Build incremental Phase 3a source_list.json: transcripts/*.txt that don't yet have
a corresponding .scribe-skills/phase3a/extracted/<name>.json.

Splits new transcripts into shorts (duration < 90s) and longs (duration >= 90s) using
the sidecar meta.json. Wave-sized batches for cron-driven runs.
"""
import json, sys, argparse
from pathlib import Path

ROOT = Path("/Users/pranavgupta/VSCode Projects/Scribe")
T = ROOT / "transcripts"
E = ROOT / ".scribe-skills" / "phase3a" / "extracted"

ap = argparse.ArgumentParser()
ap.add_argument("--wave", type=int, default=1, help="which wave (1=first 15 longs, 2=next 15)")
ap.add_argument("--size", type=int, default=15, help="agents per wave (for longs)")
ap.add_argument("--type", choices=["shorts", "longs", "all"], default="longs",
                help="which subset to materialize")
ap.add_argument("--short-cutoff", type=float, default=90.0,
                help="duration cutoff in seconds (default 90)")
ap.add_argument("--out", default=None, help="output JSON path")
args = ap.parse_args()

# Enumerate transcripts that don't yet have an extraction
txts = sorted(p for p in T.glob("*.txt"))
done_names = {p.stem for p in E.glob("*.json")}

shorts, longs = [], []
for p in txts:
    if p.stem in done_names:
        continue
    meta_path = T / f"{p.stem}.meta.json"
    duration = 0.0
    title = ""
    video_summary = ""
    if meta_path.exists():
        try:
            m = json.loads(meta_path.read_text())
            duration = float(m.get("duration_seconds", 0) or 0)
            title = m.get("title", "") or ""
            video_summary = m.get("video_summary", "") or ""
        except Exception:
            pass
    entry = {"name": p.name, "title": title, "video_summary": video_summary, "duration": duration}
    (shorts if duration < args.short_cutoff else longs).append(entry)

print(f"transcripts on disk: {len(txts)}", file=sys.stderr)
print(f"already extracted:   {len(done_names)}", file=sys.stderr)
print(f"new shorts:          {len(shorts)} (<{args.short_cutoff}s)", file=sys.stderr)
print(f"new longs:           {len(longs)} (>={args.short_cutoff}s)", file=sys.stderr)

if args.type == "shorts":
    wave = shorts
elif args.type == "longs":
    start = (args.wave - 1) * args.size
    end = start + args.size
    wave = longs[start:end]
    print(f"wave {args.wave} longs ({args.size}):  {len(wave)}", file=sys.stderr)
else:  # all
    wave = shorts + longs

out = args.out or str(ROOT / ".scribe-skills" / "phase3a" /
                     f"source_list_{args.type}_w{args.wave}.json")
Path(out).write_text(json.dumps({"sources": wave}, indent=2))
print(out)
