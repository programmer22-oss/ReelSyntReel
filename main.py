from flask import Flask, render_template, request, jsonify
import uuid
from werkzeug.utils import secure_filename
import shutil
from pathlib import Path
from waitress import serve
import logging

from processing import generate_video

# --- Configuration ---
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "user_uploads"
STATIC_REELS_DIR = BASE_DIR / "static" / "reels"
STATIC_THUMBNAILS_DIR = BASE_DIR / "static" / "thumbnails"
DONE_FILE = BASE_DIR / "done.txt"
FAILED_FILE = BASE_DIR / "failed.txt"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'mp4', 'mov', 'mp3', 'wav', 'aac'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)  # Flask config needs a string

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("flask_app.log"), logging.StreamHandler()]
)


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        job_id = request.form.get("uuid")
        desc = request.form.get("text")
        voice_id = request.form.get("voice")

        # Basic validation for the received ID
        if not job_id or ".." in job_id or "/" in job_id:
            return jsonify({"success": False, "message": "Invalid or missing job ID."}), 400

        # Create job directory first to save files
        upload_path = UPLOAD_FOLDER / job_id
        upload_path.mkdir(exist_ok=True)

        uploaded_files = request.files.getlist("files")
        durations = request.form.getlist("durations")

        input_files = []
        # Pair each file with its duration
        for i, file in enumerate(uploaded_files):
            if file and file.filename and allowed_file(file.filename):
                # Use a unique name to avoid overwrites and simplify ffmpeg input
                unique_filename = f"{uuid.uuid4().hex}{Path(secure_filename(file.filename)).suffix}"
                file.save(upload_path / unique_filename)
                input_files.append(
                    (unique_filename, durations[i] if i < len(durations) else "3"))

        # Only proceed if at least one valid file was uploaded
        if not input_files:
            return jsonify({"success": False, "message": "No valid files uploaded. Please upload images or videos."}), 400

        # Handle background music upload
        music_file = request.files.get("music")
        if music_file and music_file.filename and allowed_file(music_file.filename):
            # Save with a consistent name for the worker to find
            music_filename = "music" + \
                Path(secure_filename(music_file.filename)).suffix
            music_file.save(upload_path / music_filename)
            app.logger.info(f"Saved background music for job {job_id}")

        # Write description and voice files for the worker to use
        if desc:
            with open(upload_path / "desc.txt", "w", encoding="utf-8") as f:
                f.write(desc)

        if voice_id:
            with open(upload_path / "voice.txt", "w", encoding="utf-8") as f:
                f.write(voice_id)

        # Write the ffmpeg input file once with all filenames
        if input_files:
            with open(upload_path / "input.txt", "w") as f:
                for filename, duration in input_files:
                    f.write(f"file '{filename}'\nduration {duration}\n")

        # --- Process the video directly ---
        try:
            generate_video(job_id)
            # Since processing is synchronous, we return success upon completion.
            return jsonify({"success": True, "job_id": job_id, "message": "Reel created successfully!"})
        except Exception as e:
            app.logger.error(f"Video generation failed for job {job_id}: {e}")
            return jsonify({"success": False, "message": f"Video generation failed: {e}"}), 500

    myid = str(uuid.uuid4())
    return render_template("create.html", myid=myid)


@app.route("/gallery")
def gallery():
    try:
        # Sort reels by modification time (newest first)
        reel_files = sorted(
            [p.name for p in STATIC_REELS_DIR.iterdir() if p.is_file()],
            key=lambda f: (STATIC_REELS_DIR / f).stat().st_mtime,
            reverse=True
        )
    except FileNotFoundError:
        reel_files = []

    return render_template("gallery.html", reels=reel_files)


@app.route("/delete/<job_id>", methods=["DELETE"])
def delete_reel(job_id):
    # Security check to prevent directory traversal attacks
    if not job_id or ".." in job_id or "/" in job_id:
        return jsonify({"success": False, "message": "Invalid job ID"}), 400

    try:
        # Define paths for all associated files and folders
        reel_path = STATIC_REELS_DIR / f"{job_id}.mp4"
        thumbnail_path = STATIC_THUMBNAILS_DIR / f"{job_id}.jpg"
        job_dir = UPLOAD_FOLDER / job_id

        # Delete the files and the job directory
        reel_path.unlink(missing_ok=True)
        thumbnail_path.unlink(missing_ok=True)
        if job_dir.is_dir():
            shutil.rmtree(job_dir)

        return jsonify({"success": True, "message": "Reel deleted successfully."})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    # For development, it's better to use Flask's built-in server
    # as it provides a debugger and automatic reloading.
    # Waitress is good for production.
    # serve(app, host="0.0.0.0", port=5000)
    print("Starting Flask development server on http://127.0.0.1:5000")
    # Use debug=True for development
    app.run(host="0.0.0.0", port=5000, debug=True)
