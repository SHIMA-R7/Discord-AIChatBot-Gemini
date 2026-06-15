"""
凛 (Rin) — Discord AI電子秘書 Bot
Gemini API + VOICEVOX + Google Search Grounding
"""
from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("rin_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("rin_bot")

intents = discord.Intents.default()
intents.voice_states   = True
intents.guilds         = True
intents.members        = True
intents.message_content = True  # #会話チャンネル自動返信に必要


class RinBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.qa_cog")
        await self.load_extension("cogs.voice_cog")
        await self.load_extension("cogs.budget_cog")
        synced = await self.tree.sync()
        logger.info(f"スラッシュコマンド同期完了: {len(synced)} コマンド")

    async def on_ready(self) -> None:
        logger.info(f"凛、起動完了 — {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/q で質問受付中",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        logger.error(f"コマンドエラー: {error}", exc_info=error)
        msg = "……なんかエラーが出た。もう一度試して。"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def main() -> None:
    if not config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN が設定されていない。.env を確認して。")
        sys.exit(1)
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY が設定されていない。.env を確認して。")
        sys.exit(1)

    bot = RinBot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
