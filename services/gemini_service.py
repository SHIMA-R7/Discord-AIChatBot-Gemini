"""
Gemini API サービス (google-genai >= 1.0 対応版)

長期記憶: memory_service（SQLite + sqlite-vec）から関連記憶を取得してプロンプトに注入。
短期履歴: 直近 MAX_HISTORY ターンをインメモリで保持。
要約方式は廃止。
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)

_search_tool = types.Tool(google_search=types.GoogleSearch())

_safety_settings = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
]

# 短期履歴（インメモリ）
_histories:  dict[int, list[types.Content]] = defaultdict(list)
_turn_count: dict[int, int]                 = defaultdict(int)

# 思考タグ除去
_THOUGHT_PATTERN = re.compile(
    r"<!--.*?-->|<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thoughts(text: str) -> tuple[str, str]:
    thoughts = "\n".join(m.group() for m in _THOUGHT_PATTERN.finditer(text)).strip()
    cleaned  = _THOUGHT_PATTERN.sub("", text).strip()
    return cleaned, thoughts


def _make_config(guild_id: int, voice_mode: bool = False, memory_block: str = "") -> types.GenerateContentConfig:
    system_prompt = config.SYSTEM_PROMPT
    if memory_block:
        system_prompt = f"{system_prompt}\n\n【関連する過去の会話】\n{memory_block}"
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[_search_tool],
        safety_settings=_safety_settings,
        max_output_tokens=400 if voice_mode else 8192,
    )


async def ask(
    guild_id: int,
    user_message: str,
    voice_mode: bool = False,
) -> tuple[str, str]:
    """
    Geminiに質問して (answer, thoughts) を返す。
    関連記憶をRAGで取得してプロンプトに注入し、回答後に非同期で保存。
    """
    from services import memory_service

    # 関連記憶を検索（並列で取得）
    memory_block = await memory_service.retrieve(guild_id, user_message)

    try:
        chat = _client.aio.chats.create(
            model=config.GEMINI_MODEL,
            config=_make_config(guild_id, voice_mode, memory_block),
            history=_histories[guild_id],
        )

        response = await chat.send_message(user_message)
        raw      = response.text or "（応答が空だった……）"
        answer, thoughts = _strip_thoughts(raw)

        # 短期履歴に追加
        _histories[guild_id].append(
            types.Content(role="user",  parts=[types.Part(text=user_message)])
        )
        _histories[guild_id].append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )
        _turn_count[guild_id] += 1

        # 短期履歴トリム
        h = _histories[guild_id]
        while len(h) > config.MAX_HISTORY * 2:
            h.pop(0)
            h.pop(0)

        # 長期記憶に非同期保存（返答を遅らせない）
        import asyncio
        asyncio.create_task(memory_service.save_turn(guild_id, user_message, answer))

        return answer, thoughts

    except Exception as e:
        logger.error(f"Gemini API error (guild={guild_id}): {e}", exc_info=True)
        return f"……エラーが出た。ごめん、もう一度試して。（{type(e).__name__}）", ""


def clear_history(guild_id: int) -> None:
    """短期履歴のみクリア。長期記憶はmemory_service.clear()で別途削除。"""
    _histories[guild_id].clear()
    _turn_count[guild_id] = 0


def get_history_len(guild_id: int) -> int:
    return _turn_count[guild_id]


def get_memory_status(guild_id: int) -> dict:
    return {
        "turns":         _turn_count[guild_id],
        "short_history": len(_histories[guild_id]) // 2,
    }
