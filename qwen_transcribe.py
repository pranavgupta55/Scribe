import sys
import os
import subprocess
import threading
import time
from tqdm import tqdm
import torch
from transformers import pipeline

def setup_and_run():
    if len(sys.argv) < 2:
        print("Usage: python qwen_transcribe.py <video.mp4>")
        sys.exit(1)

    input_file = sys.argv[1]
    if not input_file.lower().endswith(".mp4"):
        print("Error: Input must be an .mp4 file")
        sys.exit(1)

    base_name = os.path.splitext(input_file)[0]
    audio_file = f"{base_name}.wav"
    output_file = f"{base_name}.txt"

    # 1. Extract Audio to 16kHz WAV (Optimal format for Qwen)
    print(f"🎬 Extracting audio from '{input_file}'...")
    subprocess.run([
        "ffmpeg", "-y", "-i", input_file,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_file, "-loglevel", "quiet"
    ], check=True)

    # Get audio duration
    dur_cmd = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_file
    ], capture_output=True, text=True)
    audio_duration = float(dur_cmd.stdout.strip())
    print(f"🎵 Audio length: {audio_duration / 60:.2f} minutes")

    # 2. Load Model on Apple Silicon
    print("🧠 Loading Qwen3-ASR-1.7B onto Apple Silicon (MPS)...")
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    # Initialize the ASR pipeline
    asr_pipeline = pipeline(
        "automatic-speech-recognition",
        model="Qwen/Qwen3-ASR-1.7B",
        device=device,
        torch_dtype=torch.float16,
        chunk_length_s=30,  # Critical for long-form reliability
        stride_length_s=5   # Prevents losing words at the chunk borders
    )

    # 3. Transcribe with Asynchronous Progress Bar
    print("✍️  Transcribing... (Accuracy optimized)")

    # Calculate estimated processing time (assuming ~12x realtime on M5)
    estimated_processing_time = audio_duration / 12.0
    result_container = []

    def run_inference():
        # Pipeline handles all chunking, batching, and merging accurately
        res = asr_pipeline(audio_file, return_timestamps=False)
        result_container.append(res)

    thread = threading.Thread(target=run_inference)
    thread.start()

    # Smart progress bar that doesn't interrupt the pipeline's internal logic
    with tqdm(total=100, desc="Transcription", bar_format="{l_bar}{bar}| {n:.1f}% [Est. Time Left: {remaining}]") as pbar:
        start_time = time.time()
        while thread.is_alive():
            elapsed = time.time() - start_time
            # Cap the visual progress at 99% until the thread actually finishes
            progress = min(99.0, (elapsed / estimated_processing_time) * 100)
            pbar.n = progress
            pbar.refresh()
            time.sleep(0.5)

        # Snap to 100% upon completion
        pbar.n = 100
        pbar.refresh()

    # 4. Save Output & Cleanup
    transcript_text = result_container[0]["text"]
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(transcript_text.strip())

    os.remove(audio_file)
    print(f"\n✅ Done! Transcript saved to: {output_file}")

if __name__ == "__main__":
    setup_and_run()
