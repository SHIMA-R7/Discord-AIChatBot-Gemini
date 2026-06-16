"""
DiscordLogHandler

Pythonのloggingをフックして、ログをDiscordの#システムチャンネルに転送する。
bot.pyのon_ready後に setup_discord_logging(bot) を呼ぶだけで有効になる。

- バッファリング（1秒間まとめて1メッセージに圧縮）でAPI呼び出しを節約
- 2000文字超えは分割
- Discordへの送信失敗はstderrに逃がす（無限ループ防止）
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import TYPE_CHECKING

import discord

import config

if TYPE_CHECKING:
    pass

_LEVEL_EMOJI = {
    logging.DEBUG:    "🔍",
    logging.INFO:     "ℹ️",
    logging.WARNING:  "⚠️",
    logging.ERROR:    "❌",
    logging.CRITICAL: "🚨",
}

# Discordへの送信自体が起こすログは転送しない（無限ループ防止）
_EXCLUDED_LOGGERS = {
    "discord",
    "discord.http",
    "discord.gateway",
    "discord.client",
    "discord.ext",
    "asyncio",
}


class DiscordLogHandler(logging.Handler):
    def __init__(self, bot: discord.Client, guild_id: int):
        super().__init__()
        self._bot      = bot
        self._guild_id = guild_id
        self._buffer:  list[str] = []
        self._task:    asyncio.Task | None = None
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # Discordライブラリ自身のログは除外
        if any(record.name.startswith(ex) for ex in _EXCLUDED_LOGGERS):
            return
        try:
            emoji = _LEVEL_EMOJI.get(record.levelno, "📋")
            line  = f"{emoji} `{self.format(record)}`"
            self._buffer.append(line)
            self._schedule_flush()
        except Exception:
            self.handleError(record)

    def _schedule_flush(self) -> None:
        if self._task is None or self._task.done():
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._flush_after_delay())
            except RuntimeError:
                pass  # イベントループがない（起動直後など）は無視

    async def _flush_after_delay(self) -> None:
        await asyncio.sleep(1.0)  # 1秒バッファリング
        if not self._buffer:
            return

        lines        = self._buffer.copy()
        self._buffer.clear()
        content      = "\n".join(lines)

        guild = self._bot.get_guild(self._guild_id)
        if not guild:
            return
        ch = discord.utils.get(guild.text_channels, name=config.CH_SYSTEM)
        if not ch:
            return

        # 2000文字超えは分割
        try:
            while content:
                await ch.send(content[:1990])
                content = content[1990:]
        except Exception as e:
            print(f"[DiscordLogHandler] 送信失敗: {e}", file=sys.stderr)


def setup_discord_logging(bot: discord.Client, guild_id: int) -> None:
    """
    ルートロガーにDiscordLogHandlerを追加する。
    bot.pyの on_ready 内で呼ぶ。
    guild_id: ログを投稿するサーバーのID
    """
    handler = DiscordLogHandler(bot, guild_id)
    handler.setLevel(logging.INFO)  # DEBUGは除外
    logging.getLogger().addHandler(handler)
    logging.getLogger("rin_bot").info("Discordログ転送を開始した")
