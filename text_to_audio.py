
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from config import ELEVENLABS_API_KEY
from elevenlabs.core import ApiError
from pathlib import Path
import logging


client = ElevenLabs(
    api_key=ELEVENLABS_API_KEY,
)


BASE_DIR = Path(__file__).resolve().parent


def text_to_speech_file(text: str, folder: str, voice_id: str) -> bool:
    # Calling the text_to_speech conversion API with detailed parameters
    response = client.text_to_speech.convert(
        voice_id=voice_id,
        output_format="mp3_22050_32",
        text=text,
        model_id="eleven_turbo_v2_5",  # use the turbo model for low latency
        # Optional voice settings that allow you to customize the output
        voice_settings=VoiceSettings(
            stability=0.0,
            similarity_boost=1.0,
            style=0.0,
            use_speaker_boost=True,
            speed=1.0,
        ),
    )

    # uncomment the line below to play the audio back
    # play(response)

    save_file_path = BASE_DIR / "user_uploads" / folder / "audio.mp3"

    # Writing the audio to a file
    try:
        with open(save_file_path, "wb") as f:
            for chunk in response:
                if chunk:
                    f.write(chunk)

        logging.info(
            f"{save_file_path}: A new audio file was saved successfully!")
        return True
    except ApiError as e:
        logging.error(f"API error during audio generation for {folder}: {e}")
        return False
    except IOError as e:
        logging.error(f"File write error for {save_file_path}: {e}")
        return False


# text_to_speech_file("Hey I am a good boy and its the python course", "ac9a7034-2bf9-11f0-b9c0-ad551e1c593a", "pNInz6obpgDQGcFmaJgB")
