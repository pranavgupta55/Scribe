# Scribe — Qwen3 ASR Transcriber for Apple Silicon

Transcribe `.mp4` files locally using [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) on your M-series Mac. Runs fully on-device via MPS (Metal Performance Shaders) with no cloud API needed.

## Setup

```bash
# Install system dependency
brew install ffmpeg

# Install Python dependencies
pip install torch torchaudio transformers accelerate tqdm
```

## Usage

```bash
python3 qwen_transcribe.py my_video.mp4
```

The transcript is saved as `my_video.txt` next to the input file.

## How it works

1. Extracts audio to a 16 kHz mono WAV using `ffmpeg`
2. Loads Qwen3-ASR-1.7B onto the MPS device in `float16`
3. Transcribes using 30-second chunks with a 5-second stride to avoid word cutoffs at boundaries
4. Shows an estimated progress bar while the model runs in a background thread
5. Saves the transcript and cleans up the temporary WAV

**First run:** the model weights (~3 GB) download from Hugging Face automatically and are cached for all future runs.

**Expected speed:** ~12× real-time on M5 (a 60-minute video finishes in ~5 minutes).
