# Scribe — Setup Guide

Transcribe any YouTube video locally in one command, powered by Qwen3-ASR-1.7B on Apple Silicon.

---

## Prerequisites

- macOS with Apple Silicon (M1 or later)
- [Homebrew](https://brew.sh) installed
- Python 3.10+

---

## Install (30 seconds)

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
bash setup.sh
source ~/.zshrc
```

That's it. `setup.sh` handles everything:
- Installs `ffmpeg` via Homebrew
- Installs `torch`, `transformers`, `yt-dlp`, and other Python deps via pip
- Adds `scribe.sh` to your PATH permanently via `~/.zshrc`

---

## Usage

```bash
scribe.sh <youtube_url> [output_dir]
```

`output_dir` is **relative to your home folder** (`~/`) and defaults to `Desktop`.

### Examples

```bash
# Saves to ~/Desktop/<video_title>.txt
scribe.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ

# Saves to ~/Documents/transcripts/<video_title>.txt
scribe.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ Documents/transcripts
```

---

## What happens when you run it

1. **Download** — `yt-dlp` fetches the best-quality audio stream as MP3 into a temp folder (no video downloaded, much faster).
2. **Convert** — `ffmpeg` converts it to 16 kHz mono WAV (optimal for the ASR model).
3. **Transcribe** — Qwen3-ASR-1.7B runs on-device via Apple's MPS (Metal) in float16, processing 30-second chunks with a 5-second stride to avoid word cutoffs at boundaries.
4. **Save** — The transcript is written as a `.txt` file next to your other files. Temp audio is deleted automatically.

**First run only:** the model weights (~3 GB) download from Hugging Face and are cached at `~/.cache/huggingface/`. All future runs load instantly from disk.

**Expected speed:** ~12× real-time on M5 (a 60-minute video ≈ 5 minutes to transcribe).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `scribe.sh: command not found` | Run `source ~/.zshrc` to reload your PATH |
| `yt-dlp: command not found` | Run `pip3 install yt-dlp` |
| `ffmpeg: command not found` | Run `brew install ffmpeg` |
| Model download hangs | Check your internet connection; the first download is ~3 GB |
| MPS not available | Ensure you're on Apple Silicon and macOS 12.3+ |
