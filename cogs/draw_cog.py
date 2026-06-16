"""
Draw Cog — 画像生成
on_messageは持たない。qa_cogから do_generate_and_reply() を呼ぶ。
プロンプトはシステムチャンネルのみに投稿（返信には含めない）。
"""
from __future__ import annotations

import io
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from services import image_service

logger = logging.getLogger(__name__)

_DRAW_PATTERN = re.compile(
    r"(絵|イラスト|画像|絵画|イメージ).{0,20}(描いて|書いて|作って|生成して|くれ|ちょうだい|ほしい)|"
    r"(描いて|書いて|イラストにして|絵にして)",
    re.IGNORECASE,
)


def is_draw_request(text: str) -> bool:
    return bool(_DRAW_PATTERN.search(text))


async def do_generate_and_reply(message: discord.Message) -> str:
    """
    画像生成してreplyする。qa_cogのon_messageから呼ばれる。
    returns: 生成した英語プロンプト（システムログ用）。失敗時は空文字。
    """
    result = await image_service.generate(message.content)
    if not result.ok:
        await message.reply(
            f"……絵を描こうとしたけど失敗した。\n```\n{result.error}\n```",
            mention_author=False,
        )
        return ""
    file = discord.File(io.BytesIO(result.image_bytes), filename="generated.png")
    await message.reply("……描いた。", file=file, mention_author=False)
    return result.prompt_en


class DrawCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="draw", description="AIに絵を描いてもらう")
    @app_commands.describe(content="描いてほしい内容（日本語でOK）")
    async def draw(self, interaction: discord.Interaction, content: str) -> None:
        await interaction.response.defer(thinking=True)
        result = await image_service.generate(content)

        if not result.ok:
            await interaction.followup.send(
                f"……絵を描くのに失敗した。ごめん。\n```\n{result.error}\n```"
            )
            return

        # プロンプトはシステムチャンネルのみ
        from services import system_log_service
        if interaction.guild:
            await system_log_service.post(
                interaction.client, interaction.guild, prompt_en=result.prompt_en
            )

        file = discord.File(io.BytesIO(result.image_bytes), filename="generated.png")
        await interaction.followup.send("……描いた。", file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DrawCog(bot))
