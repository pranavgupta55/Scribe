# Scribe — YouTube Transcriber for Apple Silicon

Transcribe any YouTube video locally with one command. Downloads audio via [yt-dlp](https://github.com/yt-dlp/yt-dlp) and transcribes on-device using [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) via Apple MPS. No cloud API, no cost.

## Quick start

```bash
git clone https://github.com/pranavgupta55/Scribe.git
cd Scribe
bash setup.sh
source ~/.zshrc
```

See [SETUP.md](SETUP.md) for full details and troubleshooting.

## Usage

```bash
# Transcript saved to ~/Desktop/<title>.txt
scribe.sh https://www.youtube.com/watch?v=...

# Transcript saved to ~/Documents/transcripts/<title>.txt
scribe.sh https://www.youtube.com/watch?v=... Documents/transcripts
```

## How it works

1. `yt-dlp` downloads the best-quality audio stream as MP3 (no video)
2. `ffmpeg` converts it to 16 kHz mono WAV
3. Qwen3-ASR-1.7B transcribes in 30-second chunks (5-second stride) on MPS in float16
4. Transcript is written to the output directory; temp files are cleaned up

**First run:** model weights (~3 GB) download from Hugging Face and are cached for all future runs.  
**Speed:** ~12× real-time on M5 — a 60-minute video takes ~5 minutes.
