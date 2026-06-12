# Gemini APIに移行済み。このファイルは互換性のために残す。
from services.gemini_service import transcribe_audio

async def transcribe(audio_bytes: bytes) -> str:
    return await transcribe_audio(audio_bytes)

def preload_model() -> None:
    pass  # Gemini APIを使うので不要