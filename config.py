import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env file

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY not found in environment variables.")
