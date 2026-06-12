"""
VOICEVOX サービス
- テキスト → WAV バイト列 を非同期で返す
- VOICEVOX Engine が localhost:50021 で起動している必要がある
"""
from __future__ import annotations

import io
import json
import logging

import aiohttp

import config

logger = logging.getLogger(__name__)

VOICEVOX_URL    = config.VOICEVOX_URL
SPEAKER_ID      = config.VOICEVOX_SPEAKER


async def synthesize(text: str, speaker: int = SPEAKER_ID) -> bytes | None:
    """
    テキストを音声合成し、WAVバイト列を返す。
    失敗時は None を返す。
    """
    # 読み上げ向けにテキスト整形（Markdownの記号を除去）
    clean = _strip_markdown(text)
    if not clean.strip():
        return None

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: audio_query（発音パラメータ生成）
            params = {"text": clean, "speaker": speaker}
            async with session.post(
                f"{VOICEVOX_URL}/audio_query",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                query = await resp.json()

            # パラメータ調整（ツンデレっぽく少し速め・高め）
            query["speedScale"]  = 1.1
            query["pitchScale"]  = 0.03
            query["intonationScale"] = 1.2

            # Step 2: synthesis（音声生成）
            async with session.post(
                f"{VOICEVOX_URL}/synthesis",
                params={"speaker": speaker},
                data=json.dumps(query),
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.read()

    except aiohttp.ClientConnectorError:
        logger.error("VOICEVOX engine に接続できない。起動しているか確認して。")
        return None
    except Exception as e:
        logger.error(f"VOICEVOX synthesis error: {e}")
        return None


def _strip_markdown(text: str) -> str:
    """Discordの基本的なMarkdown記号を読み上げ向けに除去"""
    import re
    text = re.sub(r"```[\s\S]*?```", "コードブロック省略", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*",   r"\1", text)
    text = re.sub(r"__(.+?)__",   r"\1", text)
    text = re.sub(r"~~(.+?)~~",   r"\1", text)
    text = re.sub(r"#+\s",        "",    text)
    text = re.sub(r"https?://\S+","URL省略", text)
    text = re.sub(r"[>|\-]\s",    "",    text)
    return text.strip()


async def list_speakers() -> list[dict]:
    """利用可能な話者一覧を返す（設定確認用）"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{VOICEVOX_URL}/speakers") as resp:
                resp.raise_for_status()
                return await resp.json()
    except Exception as e:
        logger.error(f"VOICEVOX speakers fetch error: {e}")
        return []
