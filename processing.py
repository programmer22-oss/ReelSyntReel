import logging
import subprocess
import shutil
from pathlib import Path
from better_profanity import profanity

from text_to_audio import text_to_speech_file

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
USER_UPLOADS = BASE_DIR / "user_uploads"
STATIC_REELS = BASE_DIR / "static" / "reels"
STATIC_THUMBNAILS = BASE_DIR / "static" / "thumbnails"
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
MAX_TEXT_LENGTH = 1500  # Max characters for TTS to prevent abuse


def _text_to_audio(job_id: str):
    """Generates audio from files in the job directory."""
    logging.info(f"Attempting to generate audio for '{job_id}'.")
    job_dir = USER_UPLOADS / job_id
    desc_path = job_dir / "desc.txt"
    voice_id_path = job_dir / "voice.txt"

    try:
        text = desc_path.read_text(encoding="utf-8").strip()
        if not text:
            logging.warning(
                f"Description file for '{job_id}' is empty. Skipping audio generation.")
            return

        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(
                f"Text exceeds the maximum length of {MAX_TEXT_LENGTH} characters.")
        if profanity.contains_profanity(text):
            raise ValueError("Inappropriate content detected in text.")

        try:
            voice_id = voice_id_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            voice_id = DEFAULT_VOICE_ID

        logging.info(
            f"Generating audio for '{job_id}' with text: {text[:50]}...")
        if not text_to_speech_file(text, job_id, voice_id):
            raise RuntimeError("Failed to generate speech file.")

    except FileNotFoundError:
        logging.warning(
            f"desc.txt not found for '{job_id}'. No audio will be generated.")


def _create_reel(job_id: str):
    """Creates a video reel using ffmpeg."""
    logging.info(f"Creating reel for '{job_id}'.")
    job_dir = USER_UPLOADS / job_id
    input_txt_path = job_dir / "input.txt"
    audio_mp3_path = job_dir / "audio.mp3"
    music_files = list(job_dir.glob("music.*"))
    music_path = music_files[0] if music_files else None
    output_mp4_path = STATIC_REELS / f"{job_id}.mp4"

    command = ['ffmpeg', '-f', 'concat',
               '-safe', '0', '-i', str(input_txt_path)]

    has_voiceover = audio_mp3_path.exists()
    has_music = music_path and music_path.exists()

    if has_voiceover:
        command.extend(['-i', str(audio_mp3_path)])
    if has_music:
        command.extend(['-i', str(music_path)])

    video_filter = 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black'
    music_only_filter = '[1:a]volume=0.3[aout]'
    mixed_audio_filter = '[1:a]aformat=fltp,volume=1.0[a1];[2:a]aformat=fltp,volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first[aout]'

    if has_voiceover and has_music:
        command.extend(
            ['-filter_complex', f'{video_filter},{mixed_audio_filter}', '-map', '0:v', '-map', '[aout]'])
    elif has_voiceover:
        command.extend(['-vf', video_filter, '-map', '0:v', '-map', '1:a'])
    elif has_music:
        command.extend(
            ['-filter_complex', f'[0:v]{video_filter}[vout];{music_only_filter}', '-map', '[vout]', '-map', '[aout]'])
    else:
        command.extend(['-vf', video_filter, '-map', '0:v'])

    command.extend(['-c:v', 'libx264', '-c:a', 'aac', '-r', '30',
                   '-pix_fmt', 'yuv420p', '-shortest', '-y', str(output_mp4_path)])

    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding='utf-8')
        logging.info(f"Reel created successfully: {output_mp4_path}")
    except subprocess.CalledProcessError as e:
        logging.error(
            f"Error creating reel for '{job_id}'. ffmpeg command failed.\nffmpeg stderr:\n{e.stderr}")
        raise


def _create_thumbnail(job_id: str):
    """Extracts the first frame of the video to use as a thumbnail."""
    logging.info(f"Creating thumbnail for '{job_id}'.")
    video_path = STATIC_REELS / f"{job_id}.mp4"
    thumbnail_path = STATIC_THUMBNAILS / f"{job_id}.jpg"

    command = ['ffmpeg', '-i', str(video_path), '-ss', '00:00:01.000',
               '-vframes', '1', '-y', str(thumbnail_path)]
    try:
        subprocess.run(command, check=True, capture_output=True,
                       text=True, encoding='utf-8')
        logging.info(f"Thumbnail created successfully: {thumbnail_path}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error creating thumbnail for '{job_id}':\n{e.stderr}")


def generate_video(job_id: str):
    """
    Main processing function to generate a video from start to finish.
    """
    logging.info(f"--- Processing new job: {job_id} ---")
    try:
        # Step 1: Generate Audio
        _text_to_audio(job_id)

        # Step 2: Create Reel
        _create_reel(job_id)

        # Step 3: Create Thumbnail
        _create_thumbnail(job_id)

        logging.info(f"--- Successfully finished processing: {job_id} ---")

    except Exception as e:
        logging.error(f"Processing for job {job_id} failed: {e}")
        # Re-raise the exception so the web server knows something went wrong.
        raise
    finally:
        # --- Final Step: Cleanup ---
        # Always try to clean up the temporary folder.
        try:
            job_dir = USER_UPLOADS / job_id
            if job_dir.is_dir():
                shutil.rmtree(job_dir)
                logging.info(f"Cleaned up temporary directory: {job_dir}")
        except Exception as cleanup_error:
            logging.warning(
                f"Could not clean up directory for {job_id}: {cleanup_error}")
