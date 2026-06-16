"""
Gemini API サービス (google-genai >= 1.0 対応版)
- Google Search Grounding 有効
- サーバーごとの会話履歴管理
- 思考タグ（<!-- -->、<think>等）を自動除去
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

_histories: dict[int, list[types.Content]] = defaultdict(list)

# 思考・メタ情報タグの正規表現
# HTMLコメント <!-- ... -->、<think>...</think>、*（アクション記述）など
_THOUGHT_PATTERN = re.compile(
    r"<!--.*?-->|<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)


def _strip_thoughts(text: str) -> tuple[str, str]:
    """
    思考部分を除去したテキストと、抽出した思考内容を返す。
    returns: (cleaned_text, thoughts)
    """
    thoughts = "\n".join(m.group() for m in _THOUGHT_PATTERN.finditer(text)).strip()
    cleaned  = _THOUGHT_PATTERN.sub("", text).strip()
    return cleaned, thoughts


def _trim_history(guild_id: int) -> None:
    h = _histories[guild_id]
    while len(h) > config.MAX_HISTORY * 2:
        h.pop(0)
        h.pop(0)


def _make_config(voice_mode: bool = False) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=config.SYSTEM_PROMPT,
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
    - answer  : 思考タグを除去した返答テキスト
    - thoughts: 抽出した思考内容（なければ空文字）
    voice_mode=True のときは200文字以内で返すようシステムプロンプトで指示済み。
    """
    # voice_modeの追加指示はシステムプロンプト側で「200文字以内」と書いてあるので
    # プロンプトへの付け足しは不要。前置き文言が混入するので削除。

    try:
        chat = _client.aio.chats.create(
            model=config.GEMINI_MODEL,
            config=_make_config(voice_mode),
            history=_histories[guild_id],
        )

        response = await chat.send_message(user_message)
        raw: str  = response.text or "（応答が空だった……）"
        answer, thoughts = _strip_thoughts(raw)

        _histories[guild_id].append(
            types.Content(role="user",  parts=[types.Part(text=user_message)])
        )
        _histories[guild_id].append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )
        _trim_history(guild_id)

        return answer, thoughts

    except Exception as e:
        logger.error(f"Gemini API error (guild={guild_id}): {e}", exc_info=True)
        return f"……エラーが出た。ごめん、もう一度試して。（{type(e).__name__}）", ""


def clear_history(guild_id: int) -> None:
    _histories[guild_id].clear()


def get_history_len(guild_id: int) -> int:
    return len(_histories[guild_id]) // 2
