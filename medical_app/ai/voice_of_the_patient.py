import logging
import os
from io import BytesIO

import speech_recognition as sr
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO)

# ------------------------------------------------
# Step 1: Record audio from microphone
# ------------------------------------------------

def record_audio(file_path, timeout=20, phrase_time_limit=None):
    recognizer = sr.Recognizer()

    try:
        # Import lazily so normal web requests do not require ffmpeg/pydub.
        from pydub import AudioSegment

        with sr.Microphone() as source:
            logging.info("Adjusting noise level...")
            recognizer.adjust_for_ambient_noise(source, duration=1)

            logging.info("Speak now...")

            audio_data = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_time_limit
            )

            wav_data = audio_data.get_wav_data()

            audio_segment = AudioSegment.from_wav(BytesIO(wav_data))
            audio_segment.export(file_path, format="mp3")

            logging.info(f"Audio saved to {file_path}")

    except Exception as e:
        logging.error(f"Recording error: {e}")


# ------------------------------------------------
# Step 2: Convert speech to text using Groq
# ------------------------------------------------

def transcribe_with_groq(stt_model, audio_filepath, GROQ_API_KEY, language="en"):

    client = Groq(api_key=GROQ_API_KEY)

    with open(audio_filepath, "rb") as audio_file:

        transcription = client.audio.transcriptions.create(
            model=stt_model,
            file=audio_file,
            language=language or "en",
        )

    return transcription.text
