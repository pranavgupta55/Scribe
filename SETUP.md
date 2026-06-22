# Scribe — Setup Guide

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
gh auth login          # browser OAuth — authenticate GitHub CLI (one-time)
bash setup.sh
source ~/.zshrc

# RAG chat uses Google Gemini (free tier). Create a key at
# https://aistudio.google.com/apikey then:
echo 'export GEMINI_API_KEY="AIza..."' >> ~/.zshrc && source ~/.zshrc
```

> Only the interactive chat needs `GEMINI_API_KEY`. Transcription, knowledge
> extraction, and retrieval all run locally and work without it.

**What `setup.sh` installs and configures:**

| Step | What | Size / Time |
|---|---|---|
| `brew install ffmpeg` | Audio extraction | ~150 MB |
| `brew install ollama` | Local LLM runtime | ~200 MB |
| `pip3 install torch torchaudio transformers ...` | ML stack | ~2 GB, ~5 min |
| `pip3 install chromadb ollama yt-dlp numpy google-genai` | Knowledge base + downloader + Gemini chat SDK | ~150 MB |
| `ollama pull qwen3:1.7b` | Lightweight extraction model (v1 `updateDB.sh`) → `models/ollama/` | **1.4 GB**, ~2 min |
| `ollama pull nomic-embed-text` | RAG embedding model → `models/ollama/` | **274 MB**, ~30 sec |
| `ollama pull qwen3-embedding:8b` | v2 graph-rebuild embedding (Phase 1a/3b/4) → `models/ollama/` | **4.7 GB**, ~5 min |
| Shell env vars | `SCRIBE_HOME`, `SCRIBE_REPO`, `HF_HOME`, `OLLAMA_MODELS`, `PATH` | instant |

All models land inside the Scribe folder under `models/` and are gitignored.
The ASR model (`whisper-large-v3-turbo`, ~1.6 GB) downloads automatically on first `scribe.sh` run into `models/hf/`.

**Total install time:** ~10 min, mostly waiting on PyTorch.

---

## Workflow

```bash
# 1. Transcribe a video → pushes transcript to GitHub
scribe.sh https://www.youtube.com/watch?v=...
scribe.sh https://www.youtube.com/watch?v=... my-filename   # explicit name

# 2. Process new transcripts into the knowledge base → pushes knowledge/ to GitHub
updateDB.sh

# 3. Explore the knowledge graph + chat with your knowledge base
serve.sh
```

---

## Setting up on a second machine

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login
bash setup.sh
source ~/.zshrc

# Repopulate local ChromaDB from existing transcripts
updateDB.sh --rebuild
```

`--rebuild` wipes `.chroma/` and reprocesses all transcripts already in the repo. Required once per new machine since the vector index is not stored in git.

---

## Ollama

Ollama must be running for `updateDB.sh` and `serve.sh` to work.

```bash
ollama serve          # start in foreground
# or: brew services start ollama   (runs as a background daemon)
```

Check it's running: `ollama list`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `scribe.sh: command not found` | `source ~/.zshrc` |
| `gh: command not found` | `brew install gh` |
| `gh` push auth error | `gh auth login` |
| `yt-dlp: command not found` | `pip3 install yt-dlp` |
| `ffmpeg: command not found` | `brew install ffmpeg` |
| ASR model download hangs | First run downloads ~1.6 GB to `models/hf/` |
| `Ollama is not running` | `ollama serve` (needs `OLLAMA_MODELS` set in env) |
| `Missing Ollama model` | `OLLAMA_MODELS=./models/ollama ollama pull qwen3:1.7b` |
| MPS not available | Requires Apple Silicon + macOS 12.3+ |
| ChromaDB empty on new machine | `updateDB.sh --rebuild` |
| Graph shows "No knowledge graph yet" | Run `updateDB.sh` first, then `serve.sh` |
| Chat says "knowledge base unavailable" | Start Ollama (`ollama serve`) and run `updateDB.sh` at least once |
| Chat says "GEMINI_API_KEY is not set" | Create a free key at https://aistudio.google.com/apikey and `export GEMINI_API_KEY=...` |
