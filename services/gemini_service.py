"""
Gemini API サービス (google-genai >= 1.0 対応版)

長期記憶アーキテクチャ:
  - 直近 MAX_HISTORY ターンを短期履歴として保持
  - 20ターンごとにその区間を要約 → recent_summaries に追加（最大5件）
  - recent_summaries が5件たまったらそれも1件に圧縮 → long_summary に統合
  - システムプロンプトに long_summary + recent_summaries を注入して長期記憶を実現

メモリ構造（guild単位）:
  _histories[gid]        : 直近の会話履歴 (Contentリスト)
  _recent_summaries[gid] : 区間要約リスト (str × 最大5件)
  _long_summary[gid]     : 圧縮済み長期要約 (str)
  _turn_count[gid]       : 総ターン数カウンタ
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

# ── メモリストア ──────────────────────────────────────────────────
_histories:        dict[int, list[types.Content]] = defaultdict(list)
_recent_summaries: dict[int, list[str]]           = defaultdict(list)
_long_summary:     dict[int, str]                 = defaultdict(str)
_turn_count:       dict[int, int]                 = defaultdict(int)

# 区間要約のトリガー間隔
SUMMARY_INTERVAL   = 20  # ターンごとに区間要約
MAX_RECENT_SUMMARY =  5  # 区間要約がこの数になったら圧縮

# 思考タグの正規表現
_THOUGHT_PATTERN = re.compile(
    r"<!--.*?-->|<think>.*?</think>|<thinking>.*?</thinking>",
    re.DOTALL | re.IGNORECASE,
)


# ── ユーティリティ ────────────────────────────────────────────────

def _strip_thoughts(text: str) -> tuple[str, str]:
    thoughts = "\n".join(m.group() for m in _THOUGHT_PATTERN.finditer(text)).strip()
    cleaned  = _THOUGHT_PATTERN.sub("", text).strip()
    return cleaned, thoughts


def _build_memory_block(guild_id: int) -> str:
    """long_summary + recent_summaries をシステムプロンプトに差し込むブロックを生成"""
    parts = []
    if _long_summary[guild_id]:
        parts.append(f"【長期記憶（圧縮済み）】\n{_long_summary[guild_id]}")
    if _recent_summaries[guild_id]:
        recent = "\n\n".join(
            f"[区間{i+1}] {s}" for i, s in enumerate(_recent_summaries[guild_id])
        )
        parts.append(f"【最近の会話要約】\n{recent}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def _make_config(guild_id: int, voice_mode: bool = False) -> types.GenerateContentConfig:
    memory_block = _build_memory_block(guild_id)
    system_prompt = config.SYSTEM_PROMPT
    if memory_block:
        system_prompt = f"{system_prompt}\n\n{memory_block}"
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[_search_tool],
        safety_settings=_safety_settings,
        max_output_tokens=400 if voice_mode else 8192,
    )


# ── 要約処理 ─────────────────────────────────────────────────────

async def _summarize_turns(turns: list[types.Content]) -> str:
    """会話履歴（Contentリスト）を1つの要約文字列に圧縮する"""
    dialog = ""
    for c in turns:
        role = "ユーザー" if c.role == "user" else "凛"
        text = c.parts[0].text if c.parts else ""
        dialog += f"{role}: {text}\n"

    prompt = (
        "以下の会話を、重要な情報・決定事項・ユーザーの特徴や好みが漏れないよう"
        "300文字以内の日本語で簡潔に要約してください。\n\n"
        f"{dialog}"
    )
    try:
        response = await _client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=400),
        )
        summary, _ = _strip_thoughts(response.text or "")
        logger.info(f"区間要約生成: {len(summary)}文字")
        return summary
    except Exception as e:
        logger.error(f"要約生成エラー: {e}", exc_info=True)
        return ""


async def _compress_summaries(guild_id: int) -> None:
    """recent_summaries 5件を1件に圧縮して long_summary に統合"""
    summaries = _recent_summaries[guild_id]
    combined  = "\n\n".join(f"[{i+1}] {s}" for i, s in enumerate(summaries))
    existing  = _long_summary[guild_id]

    prompt = (
        "以下は過去の会話の要約群です。重要な情報・ユーザーの特徴・決定事項を"
        "すべて保持しつつ、500文字以内の日本語で1つに圧縮してください。\n\n"
        + (f"【既存の長期記憶】\n{existing}\n\n" if existing else "")
        + f"【新たな要約群】\n{combined}"
    )
    try:
        response = await _client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=600),
        )
        compressed, _ = _strip_thoughts(response.text or "")
        _long_summary[guild_id]     = compressed
        _recent_summaries[guild_id] = []
        logger.info(f"長期記憶圧縮完了 (guild={guild_id}): {len(compressed)}文字")
    except Exception as e:
        logger.error(f"長期記憶圧縮エラー: {e}", exc_info=True)


async def _maybe_summarize(guild_id: int) -> None:
    """ターン数が SUMMARY_INTERVAL の倍数に達したら区間要約を実行"""
    count = _turn_count[guild_id]
    if count % SUMMARY_INTERVAL != 0:
        return

    # 直近 SUMMARY_INTERVAL ターン分を取り出して要約
    history = _histories[guild_id]
    target  = history[-(SUMMARY_INTERVAL * 2):]  # user+model で2要素/ターン
    if not target:
        return

    summary = await _summarize_turns(target)
    if not summary:
        return

    _recent_summaries[guild_id].append(summary)
    logger.info(
        f"区間要約追加 (guild={guild_id}): "
        f"recent={len(_recent_summaries[guild_id])}/{MAX_RECENT_SUMMARY}"
    )

    # recent_summaries が上限に達したら圧縮
    if len(_recent_summaries[guild_id]) >= MAX_RECENT_SUMMARY:
        await _compress_summaries(guild_id)


# ── 公開 API ─────────────────────────────────────────────────────

async def ask(
    guild_id: int,
    user_message: str,
    voice_mode: bool = False,
) -> tuple[str, str]:
    """
    Geminiに質問して (answer, thoughts) を返す。
    """
    try:
        chat = _client.aio.chats.create(
            model=config.GEMINI_MODEL,
            config=_make_config(guild_id, voice_mode),
            history=_histories[guild_id],
        )

        response = await chat.send_message(user_message)
        raw      = response.text or "（応答が空だった……）"
        answer, thoughts = _strip_thoughts(raw)

        _histories[guild_id].append(
            types.Content(role="user",  parts=[types.Part(text=user_message)])
        )
        _histories[guild_id].append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )
        _turn_count[guild_id] += 1

        # 直近履歴は MAX_HISTORY ターン分だけ保持（古いものは要約済みなので捨ててOK）
        h = _histories[guild_id]
        while len(h) > config.MAX_HISTORY * 2:
            h.pop(0)
            h.pop(0)

        # 非同期で要約処理（返答を遅らせないようにcreate_task）
        import asyncio
        asyncio.create_task(_maybe_summarize(guild_id))

        return answer, thoughts

    except Exception as e:
        logger.error(f"Gemini API error (guild={guild_id}): {e}", exc_info=True)
        return f"……エラーが出た。ごめん、もう一度試して。（{type(e).__name__}）", ""


def clear_history(guild_id: int) -> None:
    _histories[guild_id].clear()
    _recent_summaries[guild_id].clear()
    _long_summary[guild_id] = ""
    _turn_count[guild_id]   = 0


def get_history_len(guild_id: int) -> int:
    return _turn_count[guild_id]


def get_memory_status(guild_id: int) -> dict:
    """デバッグ・/qstatus用"""
    return {
        "turns":           _turn_count[guild_id],
        "short_history":   len(_histories[guild_id]) // 2,
        "recent_summaries": len(_recent_summaries[guild_id]),
        "has_long_summary": bool(_long_summary[guild_id]),
        "long_summary_len": len(_long_summary[guild_id]),
    }
