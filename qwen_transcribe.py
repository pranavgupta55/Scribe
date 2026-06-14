import sys
import os
import subprocess
import threading
import time
from tqdm import tqdm
import torch
from transformers import pipeline

# whisper-large-v3-turbo: ~1.6 GB, natively supported, ~8-12x realtime on Apple Silicon
# Switch to Qwen/Qwen3-ASR-1.7B once transformers adds native qwen3_asr support
ASR_MODEL = "openai/whisper-large-v3-turbo"

AUDIO_EXTS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm"}

def setup_and_run():
    if len(sys.argv) < 2:
        print("Usage: python3 qwen_transcribe.py <audio_or_video_file> [output.txt]")
        sys.exit(1)

    input_file = sys.argv[1]
    ext = os.path.splitext(input_file)[1].lower()
    if ext not in AUDIO_EXTS:
        print(f"Error: Unsupported file type '{ext}'. Supported: {', '.join(sorted(AUDIO_EXTS))}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        output_file = os.path.splitext(input_file)[0] + ".txt"

    wav_file = os.path.splitext(input_file)[0] + "_16k.wav"

    print(f"🎬 Converting to 16 kHz WAV...")
    subprocess.run([
        "ffmpeg", "-y", "-i", input_file,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        wav_file, "-loglevel", "quiet"
    ], check=True)

    dur_cmd = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", wav_file
    ], capture_output=True, text=True)
    audio_duration = float(dur_cmd.stdout.strip())
    print(f"🎵 Audio length: {audio_duration / 60:.2f} minutes")

    print(f"🧠 Loading {ASR_MODEL} onto Apple Silicon (MPS)...")
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    asr_pipeline = pipeline(
        "automatic-speech-recognition",
        model=ASR_MODEL,
        device=device,
        torch_dtype=torch.float16,
        chunk_length_s=30,
        stride_length_s=5,
    )

    print("✍️  Transcribing...")
    estimated_processing_time = audio_duration / 10.0
    result_container = []

    def run_inference():
        res = asr_pipeline(
            wav_file,
            return_timestamps=False,
            generate_kwargs={"language": "english"},
        )
        result_container.append(res)

    thread = threading.Thread(target=run_inference)
    thread.start()

    with tqdm(total=100, desc="Transcription", bar_format="{l_bar}{bar}| {n:.1f}% [Est. left: {remaining}]") as pbar:
        start_time = time.time()
        while thread.is_alive():
            elapsed = time.time() - start_time
            pbar.n = min(99.0, (elapsed / estimated_processing_time) * 100)
            pbar.refresh()
            time.sleep(0.5)
        pbar.n = 100
        pbar.refresh()

    transcript_text = result_container[0]["text"]
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(transcript_text.strip())

    if os.path.exists(wav_file):
        os.remove(wav_file)

    print(f"\n✅ Transcript saved to: {output_file}")

if __name__ == "__main__":
    setup_and_run()
