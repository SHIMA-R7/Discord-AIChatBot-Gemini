"""
Gemini API サービス  (google-genai >= 1.0 対応版)
- Google Search Grounding 有効
- サーバーごとの会話履歴管理
- 完全非同期 (client.aio)
"""
from __future__ import annotations

import logging
from collections import defaultdict

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

# ── クライアント初期化（モジュールロード時に1回だけ） ──────────────
_client = genai.Client(api_key=config.GEMINI_API_KEY)

# ── Google Search Grounding ツール ─────────────────────────────────
_search_tool = types.Tool(google_search=types.GoogleSearch())

# ── 安全フィルタ ───────────────────────────────────────────────────
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

# ── サーバー（guild_id）ごとの会話履歴 ────────────────────────────
# 形式: { guild_id: [ types.Content(...), ... ] }
_histories: dict[int, list[types.Content]] = defaultdict(list)


def _trim_history(guild_id: int) -> None:
    """履歴が上限を超えたら古いターン（2件ずつ）を削除"""
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
) -> str:
    """
    Gemini に質問して応答テキストを返す。
    voice_mode=True のときは200文字以内に要約するよう指示を追加。
    """
    prompt = user_message
    if voice_mode:
        prompt += "\n\n【重要】ボイスチャンネルでの返答です。200文字以内の日本語で簡潔にまとめてください。"

    try:
        # 毎回履歴を渡して新しい AsyncChat を作る
        chat = _client.aio.chats.create(
            model=config.GEMINI_MODEL,
            config=_make_config(voice_mode),
            history=_histories[guild_id],
        )

        response = await chat.send_message(prompt)
        answer: str = response.text or "（応答が空だった……）"

        # 履歴を更新（Content オブジェクトで保持）
        _histories[guild_id].append(
            types.Content(role="user",  parts=[types.Part(text=user_message)])
        )
        _histories[guild_id].append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )
        _trim_history(guild_id)

        return answer

    except Exception as e:
        logger.error(f"Gemini API error (guild={guild_id}): {e}", exc_info=True)
        return f"……エラーが出た。ごめん、もう一度試して。（{type(e).__name__}）"


def clear_history(guild_id: int) -> None:
    """指定サーバーの会話履歴をリセット"""
    _histories[guild_id].clear()


def get_history_len(guild_id: int) -> int:
    """現在の会話ターン数を返す"""
    return len(_histories[guild_id]) // 2


# ── 音声文字起こし ─────────────────────────────────────────────────
# 【修正】モデルを config.GEMINI_MODEL に統一
#         音声対応モデル（gemini-2.0-flash 以降）が必要。
#         gemini-2.5-flash-preview-04-17 は廃止済みのため動作しない。
# ─────────────────────────────────────────────────────────────────
# 音声入力に対応しているモデルかどうかを確認するため、
# config.GEMINI_MODEL が "gemini-2.0-flash" 以降であることを前提とする。
# .env の GEMINI_MODEL を以下のいずれかに変更すること:
#   gemini-2.0-flash        ← 軽量・推奨
#   gemini-2.5-flash        ← 高精度

async def transcribe_audio(wav_bytes: bytes) -> str:
    """
    WAVバイト列をGemini APIに直接投げて文字起こしする。
    faster-whisper不要。
    """
    import base64
    try:
        audio_b64 = base64.b64encode(wav_bytes).decode()

        response = await _client.aio.models.generate_content(
            # 【修正】ハードコードされたモデル名を config.GEMINI_MODEL に統一
            model=config.GEMINI_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="audio/wav",
                                data=audio_b64,
                            )
                        ),
                        types.Part(text="この音声を日本語でそのまま文字起こしして。文字起こし結果だけ返して、他の説明は不要。"),
                    ],
                )
            ],
        )
        text = (response.text or "").strip()
        logger.info(f"音声文字起こし: 「{text}」")
        return text
    except Exception as e:
        logger.error(f"音声文字起こしエラー: {e}", exc_info=True)
        return ""
