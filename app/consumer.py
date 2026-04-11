"""
Fluxo 2: RabbitMQ -> IA -> Resposta WhatsApp
Consome mensagens da fila, processa com Gemini e responde via UAZAPI.
"""
import asyncio
import logging
import re

from app.config import settings
from app.images import MEDIA_DICT
from app.services import redis_service as rds
from app.services import uazapi
from app.services.gemini import chat as gemini_chat, transcribe_audio, analyze_image
from app.services.rabbitmq import consume

logger = logging.getLogger(__name__)

# Tipos de mensagem de texto
TEXT_TYPES = {"ExtendedTextMessage", "Conversation", "ContactMessage", "ReactionMessage"}

# Numeros que nao passam pelo debounce de mensagens
DEBOUNCE_BYPASS = {"5511989887525"}


# ---- helpers ----

def _is_group(chat_id: str) -> bool:
    return "@g.us" in chat_id


def _parse_ai_response(text: str) -> tuple[list[dict], bool]:
    """
    Parseia a resposta da IA:
    - Extrai flag [FINALIZADO=0/1]
    - Quebra em partes (por \\n\\n ou |||)
    - Detecta tags de midia e substitui pelos links do dicionario
    Retorna (partes, finalizado).
    """
    # Extrair flag FINALIZADO
    finalizado = False
    match = re.search(r"\[FINALIZADO=(\d)\]", text)
    if match:
        finalizado = match.group(1) == "1"
        text = re.sub(r"\[FINALIZADO=\d\]", "", text).strip()

    # Quebrar em partes
    if "|||" in text:
        raw_parts = [p.strip() for p in text.split("|||") if p.strip()]
    else:
        raw_parts = [p.strip() for p in text.split("\n\n") if p.strip()]

    parts = []
    for part in raw_parts:
        # Checa se e uma tag de midia do dicionario
        tag_match = re.search(r"\[([A-Z_]+)\]", part)
        if tag_match and f"[{tag_match.group(1)}]" in MEDIA_DICT:
            tag = f"[{tag_match.group(1)}]"
            media = MEDIA_DICT[tag]
            parts.append({"type": media["type"], "content": media["url"]})
        else:
            parts.append({"type": "text", "content": part})

    if not parts:
        parts = [{"type": "text", "content": text}]

    return parts, finalizado


# ---- processamento principal ----

async def _process_message(msg: dict) -> None:
    phone = msg["phone"]
    chat_id = msg["chat_id"]
    from_me = msg.get("from_me", False)
    msg_type = msg.get("msg_type", "")
    msg_text = msg.get("msg", "")
    push_name = msg.get("push_name", "")

    # B) Mensagem propria -> bloqueia agente por 1h
    if from_me:
        await rds.set_block(phone)
        logger.info("Humano assumiu chat %s - agente bloqueado por 1h", chat_id)
        return

    # C) Verifica bloqueio ativo
    if await rds.is_blocked(phone):
        logger.info("Agente bloqueado para %s - ignorando", chat_id)
        return

    # D) Filtra grupos
    if _is_group(chat_id):
        return

    # D.1) Comando /reset - limpa historico, lead e buffer
    if msg_type in TEXT_TYPES and (msg_text or "").strip().lower() == "/reset":
        await rds.clear_chat_history(phone)
        await rds.delete_lead(phone)
        await rds.delete_buffer(phone)
        logger.info("Reset solicitado para %s", phone)
        try:
            await uazapi.send_text(phone, "Conversa reiniciada.")
        except Exception:
            logger.exception("Erro ao confirmar reset para %s", phone)
        return

    # Cadastro de lead
    lead = await rds.get_lead(phone)
    if not lead:
        lead = await rds.create_lead(phone, push_name)

    if push_name and lead.get("name", "") != push_name:
        await rds.update_lead(phone, name=push_name)

    # E) Identificacao do tipo de mensagem
    if msg_type in TEXT_TYPES:
        buffer_text = msg_text
    elif msg_type == "AudioMessage":
        try:
            raw_msg = msg.get("raw_message", {})
            audio_url = raw_msg.get("audioMessage", {}).get("url", "")
            if audio_url:
                audio_bytes = await uazapi.download_media(audio_url)
                transcription = await transcribe_audio(audio_bytes)
                buffer_text = f"[Audio transcrito]: {transcription}"
            else:
                buffer_text = "[Audio recebido - nao foi possivel transcrever]"
        except Exception:
            logger.exception("Erro ao transcrever audio")
            buffer_text = "[Audio recebido - erro na transcricao]"
    elif msg_type == "ImageMessage":
        try:
            raw_msg = msg.get("raw_message", {})
            image_url = raw_msg.get("imageMessage", {}).get("url", "")
            caption = raw_msg.get("imageMessage", {}).get("caption", "")
            if image_url:
                image_bytes = await uazapi.download_media(image_url)
                description = await analyze_image(image_bytes)
                buffer_text = f"[Imagem recebida]: {description}"
                if caption:
                    buffer_text += f"\nLegenda: {caption}"
            else:
                buffer_text = "[Imagem recebida - nao foi possivel analisar]"
        except Exception:
            logger.exception("Erro ao analisar imagem")
            buffer_text = "[Imagem recebida - erro na analise]"
    else:
        buffer_text = msg_text or f"[Mensagem do tipo {msg_type} recebida]"

    if not buffer_text:
        return

    # F) Buffer de mensagens (debounce)
    count = await rds.push_buffer(phone, buffer_text)

    if count > 1:
        # Outra execucao ja esta esperando
        logger.info("Buffer ja ativo para %s (count=%d) - saindo", phone, count)
        return

    # Primeira mensagem - espera debounce para acumular
    if phone not in DEBOUNCE_BYPASS:
        await asyncio.sleep(settings.DEBOUNCE_SECONDS)

    # Pega todas as mensagens acumuladas
    messages = await rds.get_buffer(phone)
    await rds.delete_buffer(phone)

    unified_msg = "\n".join(messages)
    logger.info("Mensagem unificada de %s: %s", phone, unified_msg[:100])

    # G) Processamento com IA (com retry)
    ai_response = ""
    for attempt in range(6):
        try:
            ai_response = await gemini_chat(phone, unified_msg, lead.get("name", ""))
        except Exception:
            logger.exception("Erro no Gemini (tentativa %d)", attempt + 1)

        if ai_response:
            break
        logger.warning("Resposta vazia do Gemini (tentativa %d/6)", attempt + 1)
        await asyncio.sleep(2)

    if not ai_response:
        logger.error("Gemini retornou vazio apos 6 tentativas para %s", phone)
        return

    # H) Verifica bloqueio pos-IA
    if await rds.is_blocked(phone):
        logger.info("Humano assumiu durante processamento IA - descartando resposta para %s", phone)
        return

    # I) Parsing e envio
    parts, finalizado = _parse_ai_response(ai_response)

    for part in parts:
        try:
            if part["type"] == "text":
                await uazapi.send_text(phone, part["content"])
            elif part["type"] == "image":
                await uazapi.send_image(phone, part["content"])
                await asyncio.sleep(3)
            elif part["type"] == "document":
                await uazapi.send_document(phone, part["content"])
            elif part["type"] == "video":
                await uazapi.send_video(phone, part["content"])
        except Exception:
            logger.exception("Erro ao enviar %s para %s", part["type"], phone)

    # J) Pos-envio: finalizacao
    if finalizado:
        await rds.set_block(phone)
        await rds.update_lead(phone, status_conversa="Finalizado")
        logger.info("Conversa finalizada para %s", phone)


async def start_consumer() -> None:
    """Inicia o consumer RabbitMQ."""
    await consume(_process_message)
