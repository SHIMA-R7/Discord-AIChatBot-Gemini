"""
#システム チャンネルへの転送サービス

以下をまとめて投稿する:
  - Geminiの思考内容（<!-- --> や <think> タグ）
  - 画像生成プロンプト（英語）
  - その他ログ情報
"""
from __future__ import annotations

import logging

import discord

import config

logger = logging.getLogger(__name__)


async def post(
    bot: discord.Client,
    guild: discord.Guild,
    *,
    thoughts:   str = "",
    prompt_en:  str = "",
    extra:      str = "",
) -> None:
    """
    CH_SYSTEM チャンネルにログを投稿する。
    全て空なら何もしない。
    """
    lines = []
    if thoughts:
        lines.append(f"🧠 **思考ログ**\n```\n{thoughts[:1800]}\n```")
    if prompt_en:
        lines.append(f"🎨 **画像プロンプト（EN）**\n```\n{prompt_en}\n```")
    if extra:
        lines.append(f"📋 **その他**\n{extra}")

    if not lines:
        return

    ch_name = config.CH_SYSTEM
    ch = discord.utils.get(guild.text_channels, name=ch_name)
    if not ch:
        logger.warning(f"#{ch_name} チャンネルが見つからない (guild={guild.id})")
        return

    try:
        await ch.send("\n".join(lines))
    except discord.Forbidden:
        logger.warning(f"#{ch_name} への書き込み権限がない (guild={guild.id})")
    except Exception as e:
        logger.error(f"システムログ投稿エラー: {e}", exc_info=True)
