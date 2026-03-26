import asyncio
import edge_tts


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
            text=input_text,
            voice=voice
        )

        await communicate.save(output_filepath)

    asyncio.run(generate_voice())