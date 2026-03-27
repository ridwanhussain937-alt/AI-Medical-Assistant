import asyncio
import re

import edge_tts


def _build_tts_text(input_text):
    # Strip markdown-style formatting and compress whitespace so the TTS provider
    # receives a short, plain-language summary instead of raw rich-text output.
    cleaned_text = re.sub(r"[*_`#>\-]+", " ", str(input_text or ""))
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    if not cleaned_text:
        raise ValueError("No text was available for voice synthesis.")

    return cleaned_text[:1800]


def text_to_speech_with_edge(input_text, output_filepath, language="english"):

    voice_map = {

        "english":"en-US-AriaNeural",
        "hindi":"hi-IN-SwaraNeural",
        "urdu":"ur-PK-UzmaNeural",
        "arabic":"ar-SA-ZariyahNeural",
        "bengali":"bn-BD-NabanitaNeural",
        "hinglish":"en-US-AriaNeural"
    }

    voice=voice_map.get(language,"en-US-AriaNeural")

    async def generate_voice():

        communicate=edge_tts.Communicate(
            text=_build_tts_text(input_text),
            voice=voice
        )

        await communicate.save(output_filepath)

    asyncio.run(generate_voice())
