import logging
import io

import google.generativeai as genai

from app.config import settings
from app.prompt import SYSTEM_PROMPT
from app.services.redis_service import get_chat_history, append_chat_history

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured() -> None:
    global _configured
    if not _configured:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _configured = True


async def chat(phone: str, user_message: str, lead_name: str = "") -> str:
    _ensure_configured()

    history = await get_chat_history(phone)

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=SYSTEM_PROMPT,
    )

    chat_session = model.start_chat(history=history)
    response = chat_session.send_message(user_message)
    ai_text = response.text.strip() if response.text else ""

    await append_chat_history(phone, "user", user_message)
    if ai_text:
        await append_chat_history(phone, "model", ai_text)

    return ai_text


async def transcribe_audio(audio_bytes: bytes) -> str:
    _ensure_configured()
    model = genai.GenerativeModel("gemini-2.5-flash")

    audio_part = {
        "mime_type": "audio/ogg",
        "data": audio_bytes,
    }
    response = model.generate_content(
        ["Transcreva essa gravacao de audio fielmente. Retorne APENAS o texto transcrito, sem comentarios.", audio_part]
    )
    return response.text.strip() if response.text else ""


async def analyze_image(image_bytes: bytes) -> str:
    _ensure_configured()
    model = genai.GenerativeModel("gemini-2.5-flash")

    image_part = {
        "mime_type": "image/jpeg",
        "data": image_bytes,
    }
    response = model.generate_content(
        ["Descreva esta imagem em ate 50 palavras, em portugues.", image_part]
    )
    return response.text.strip() if response.text else ""
