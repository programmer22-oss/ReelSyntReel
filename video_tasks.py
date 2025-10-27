import logging
import subprocess
import shutil
from pathlib import Path
import json
from better_profanity import profanity
from celery import Celery
from celery.signals import worker_process_init

from text_to_audio import text_to_speech_file

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
USER_UPLOADS = BASE_DIR / "user_uploads"
STATIC_REELS = BASE_DIR / "static" / "reels"
STATIC_THUMBNAILS = BASE_DIR / "static" / "thumbnails"
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
MAX_TEXT_LENGTH = 1500  # Max characters for TTS to prevent abuse

# --- Celery App Definition ---
# The first argument is the project name, broker is the Redis URL,
# and backend is where results are stored.
celery_app = Celery(
    'video_tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

# --- Logging Setup ---
# We set up logging when a worker process starts.


@worker_process_init.connect
def setup_logging(**kwargs):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("celery_processor.log"),
            logging.StreamHandler()
        ]
    )

# --- Helper Functions (Adapted from generate_process.py) ---


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
            return True

        # Security checks
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
        return text_to_speech_file(text, job_id, voice_id)

    except FileNotFoundError:
        logging.warning(
            f"desc.txt not found for '{job_id}'. No audio will be generated.")
        return True  # Not a fatal error if no text is provided


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

    # --- Define Filters and Mappings ---
    video_filter = 'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black'

    # Define audio filters for clarity
    music_only_filter = '[1:a]volume=0.3[aout]'
    mixed_audio_filter = '[1:a]aformat=fltp,volume=1.0[a1];[2:a]aformat=fltp,volume=0.15[a2];[a1][a2]amix=inputs=2:duration=first[aout]'

    if has_voiceover and has_music:
        # Voiceover is input 1, Music is input 2
        # Apply complex filter and map the correct streams to the output
        command.extend(['-filter_complex', f'{video_filter},{mixed_audio_filter}',
                       '-map', '0:v', '-map', '[aout]'])
    elif has_voiceover:
        # Voiceover is input 1
        command.extend(['-vf', video_filter, '-map', '0:v', '-map', '1:a'])
    elif has_music:
        # Music is input 1
        # Apply video filter to input 0, audio filter to input 1, and map the results
        command.extend(['-filter_complex', f'[0:v]{video_filter}[vout];{music_only_filter}',
                        '-map', '[vout]', '-map', '[aout]'])
    else:
        # Map video only, no audio
        command.extend(['-vf', video_filter, '-map', '0:v'])

    # --- Add Output Encoding Options and Output File ---
    command.extend(['-c:v', 'libx264', '-c:a', 'aac', '-r', '30',
                   '-pix_fmt', 'yuv420p', '-shortest', '-y', str(output_mp4_path)])

    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding='utf-8')
        logging.info(f"Reel created successfully: {output_mp4_path}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(
            f"Error creating reel for '{job_id}'. ffmpeg command failed.")
        logging.error(f"ffmpeg stderr:\n{e.stderr}")
        raise  # Re-raise the exception to fail the Celery task


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
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error creating thumbnail for '{job_id}':\n{e.stderr}")
        return False  # Don't fail the whole job for a thumbnail


# --- The Celery Task ---
@celery_app.task(bind=True)
def process_video_task(self, job_id: str):
    """
    This is the main Celery task that processes a video job.
    The `bind=True` argument makes `self` available to update task state.
    """
    logging.info(f"--- Processing new job: {job_id} ---")
    try:
        # Step 1: Generate Audio
        self.update_state(state='PROGRESS', meta={
                          'message': 'Generating audio...', 'progress': 30})
        _text_to_audio(job_id)

        # Step 2: Create Reel
        self.update_state(state='PROGRESS', meta={
                          'message': 'Creating video...', 'progress': 60})
        _create_reel(job_id)

        # Step 3: Create Thumbnail
        self.update_state(state='PROGRESS', meta={
                          'message': 'Generating thumbnail...', 'progress': 90})
        _create_thumbnail(job_id)

        # --- Final Step: Cleanup ---
        # The job is done, so we can remove the temporary upload folder.
        try:
            job_dir = USER_UPLOADS / job_id
            if job_dir.is_dir():
                shutil.rmtree(job_dir)
                logging.info(f"Cleaned up temporary directory: {job_dir}")
        except Exception as cleanup_error:
            logging.warning(
                f"Could not clean up directory for {job_id}: {cleanup_error}")

        logging.info(f"--- Successfully finished processing: {job_id} ---")
        return {'message': 'Complete', 'progress': 100}

    except Exception as e:
        logging.error(f"Task for job {job_id} failed: {e}")
        # This will mark the task as FAILED in Celery
        # The frontend will receive the 'FAILURE' state.
        self.update_state(
            state='FAILURE',
            meta={'message': str(
                e) or 'An unknown error occurred.', 'progress': -1}
        )
        # Also attempt to clean up on failure
        try:
            job_dir = USER_UPLOADS / job_id
            if job_dir.is_dir():
                shutil.rmtree(job_dir)
                logging.info(
                    f"Cleaned up temporary directory after failure: {job_dir}")
        except Exception as cleanup_error:
            logging.warning(
                f"Could not clean up directory for {job_id} after failure: {cleanup_error}")

        # Re-raise the exception to ensure Celery knows it's a failure
        raise


if __name__ == "__main__":
    # This allows you to test the task directly if needed,
    # but it should be run by a Celery worker.
    print("This file defines Celery tasks and is not meant to be run directly.")
    print("Start a worker with: celery -A video_tasks worker --loglevel=info")
