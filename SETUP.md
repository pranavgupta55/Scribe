# Scribe — Setup Guide

Full setup for the transcription + knowledge base pipeline. Designed to be completed in one terminal session.

---

## Prerequisites

- macOS with Apple Silicon (M1 or later)
- [Homebrew](https://brew.sh) — if missing: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
- Python 3.10+ — check with `python3 --version`

---

## Install

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login          # authenticate GitHub CLI (browser flow, one-time)
bash setup.sh
source ~/.zshrc
```

**What `setup.sh` does:**

| Step | What | Size/Time |
|---|---|---|
| `brew install ffmpeg` | Audio extraction | ~150 MB |
| `brew install ollama` | Local LLM runtime | ~200 MB |
| `pip3 install ...` | Python packages | ~2 GB (PyTorch + deps) |
| `pip3 install chromadb ollama` | Knowledge base libs | ~50 MB |
| `ollama pull qwen3:1.7b` | Extraction model | ~1.1 GB, ~1.5 min |
| `ollama pull nomic-embed-text` | Embedding model | ~274 MB, ~25 sec |
| Shell env vars | `SCRIBE_HOME`, `SCRIBE_REPO`, `PATH` | instant |

The ASR model (3.4 GB) downloads automatically on your **first** `scribe.sh` run.

**Total install time:** ~10–15 min on fast internet, mostly waiting on PyTorch.

---

## Workflow

### Step 1 — Transcribe a video

```bash
# Filename derived from video title
scribe.sh https://www.youtube.com/watch?v=...

# Explicit filename (no extension needed)
scribe.sh https://www.youtube.com/watch?v=... my-interview
```

This downloads audio, transcribes on-device, and pushes `transcripts/<filename>.txt` to GitHub. Nothing is saved locally. The **first run** pauses ~5 min to download the ASR model.

### Step 2 — Update the knowledge base

```bash
updateDB.sh
```

Pulls the latest transcripts from GitHub, processes any new ones (chunk → extract → embed → synthesise), and pushes the updated `knowledge/` directory. Run this after one or more `scribe.sh` calls.

### Setting up on a second machine

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login
bash setup.sh
source ~/.zshrc

# Rebuild ChromaDB from the existing transcripts
updateDB.sh --rebuild
```

`--rebuild` wipes the local `.chroma/` index and reprocesses all transcripts in the repo, repopulating the vector store from scratch. Required once per new machine since ChromaDB is not stored in git.

---

## Ollama

Ollama must be running for `updateDB.sh` to work. Start it with:

```bash
ollama serve          # runs in the foreground
# or open the Ollama app from Applications
```

To check it's running: `ollama list` (should show your installed models).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `scribe.sh: command not found` | `source ~/.zshrc` |
| `gh: command not found` | `brew install gh` |
| `gh` auth error on push | `gh auth login` |
| `yt-dlp: command not found` | `pip3 install yt-dlp` |
| `ffmpeg: command not found` | `brew install ffmpeg` |
| ASR model download hangs | Check internet; download is ~3.4 GB |
| `Ollama is not running` | `ollama serve` or open Ollama app |
| `Missing Ollama model` | `ollama pull qwen3:1.7b && ollama pull nomic-embed-text` |
| MPS not available | Requires Apple Silicon + macOS 12.3+ |
| ChromaDB empty on new machine | `updateDB.sh --rebuild` |
| Wrong facts in topic files | `updateDB.sh --rebuild` re-extracts everything |
