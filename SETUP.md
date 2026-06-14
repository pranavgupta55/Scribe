# Scribe — Setup Guide

Transcribe any YouTube video locally in one command. Transcript is pushed directly to GitHub — no local files left behind.

---

## Prerequisites

- macOS with Apple Silicon (M1 or later)
- [Homebrew](https://brew.sh) installed
- Python 3.10+

---

## Install

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
gh auth login          # authenticate GitHub CLI (one-time)
bash setup.sh
source ~/.zshrc
```

`setup.sh` handles everything:
- Installs `ffmpeg` and the GitHub CLI (`gh`) via Homebrew
- Installs `torch`, `transformers`, `yt-dlp`, and other Python deps
- Sets `SCRIBE_HOME`, `SCRIBE_REPO`, and adds `scribe.sh` to your PATH in `~/.zshrc`

---

## Usage

```bash
scribe.sh <youtube_url> [output_filename]
```

`output_filename` is optional — defaults to a sanitised version of the video title. The `.txt` extension is added automatically. The transcript is uploaded to `transcripts/` in the GitHub repo; nothing is saved locally.

### Examples

```bash
# Filename derived from video title
scribe.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Explicit filename
scribe.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ my-interview
```

---

## What happens when you run it

1. **Download** — `yt-dlp` fetches the audio stream as MP3 into a temp folder (no video, much faster).
2. **Convert** — `ffmpeg` converts it to 16 kHz mono WAV (optimal for the ASR model).
3. **Transcribe** — Qwen3-ASR-1.7B runs on-device via Apple MPS (Metal) in float16, in 30-second chunks with a 5-second stride to prevent word cutoffs.
4. **Upload** — The transcript is pushed directly to `transcripts/<filename>.txt` in the GitHub repo via the API. All temp files are deleted.

**First run only:** model weights (~3 GB) download from Hugging Face and cache at `~/.cache/huggingface/`. All future runs load instantly.

**Speed:** ~12× real-time on M5 — a 60-minute video takes ~5 minutes.

---

## Syncing across machines

Because transcripts live in GitHub, syncing is automatic. On any machine with the repo cloned and `setup.sh` run, `scribe.sh` uploads and any other machine can `git pull` to get new transcripts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `scribe.sh: command not found` | Run `source ~/.zshrc` |
| `gh: command not found` | Run `brew install gh` |
| `gh` auth error | Run `gh auth login` |
| `yt-dlp: command not found` | Run `pip3 install yt-dlp` |
| `ffmpeg: command not found` | Run `brew install ffmpeg` |
| Model download hangs | Check internet connection; first download is ~3 GB |
| MPS not available | Requires Apple Silicon and macOS 12.3+ |
