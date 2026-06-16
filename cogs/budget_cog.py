"""
Budget Cog — レシート解析 & Notion家計簿登録
返信は1回にまとめる（JSON + Embed + Notion結果を同時送信）
"""
from __future__ import annotations

import json
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from services import receipt_service, notion_service

logger = logging.getLogger(__name__)


class BudgetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="budget", description="レシート家計簿の使い方を表示する")
    async def budget_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"**レシート解析の使い方**\n"
            f"① `#{config.CH_BUDGET}` チャンネルにレシート画像を投稿（自動解析）\n"
            f"② どこでも `!budget` と書いて画像を添付して送る",
            ephemeral=True,
        )

    @commands.command(name="budget")
    async def budget_prefix(self, ctx: commands.Context) -> None:
        images = [a for a in ctx.message.attachments
                  if a.content_type and a.content_type.startswith("image/")]
        if not images:
            await ctx.reply("……レシートの画像が添付されていない。", mention_author=False)
            return
        await _process_receipt(ctx.message, images[0])

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.channel.name != config.CH_BUDGET:
            return
        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return
        await _process_receipt(message, images[0])


async def _process_receipt(message: discord.Message, attachment: discord.Attachment) -> None:
    msg = await message.reply("……解析中。少し待って。", mention_author=False)

    # 1. 画像ダウンロード
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                image_bytes = await resp.read()
    except Exception as e:
        await msg.edit(content=f"……画像のダウンロードに失敗した。（{e}）")
        return

    # 2. Gemini解析
    result = await receipt_service.analyze(image_bytes)

    if result.error:
        await msg.edit(content=f"……解析でエラーが出た。ごめん。\n```\n{result.error}\n```")
        return
    if not result.items:
        await msg.edit(content="……レシートから品目を読み取れなかった。画像がぼけてたりしない？")
        return

    # 3. Notion登録（返信より先にやっておく）
    notion_status = ""
    if not notion_service.NOTION_TOKEN or not notion_service.NOTION_DATABASE_ID:
        notion_status = "\n⚠️ Notion未設定。`.env` に `NOTION_TOKEN` と `NOTION_DATABASE_ID` を追加して。"
    else:
        notion_items = [
            {"name": i.name, "price": i.price, "category": i.category, "date": i.date}
            for i in result.items
        ]
        ok, ng = await notion_service.add_items(notion_items)
        if ng == 0:
            notion_status = f"\n✅ Notionに **{ok}件** 登録した。"
        else:
            notion_status = f"\n⚠️ Notionに {ok}件登録、{ng}件失敗。ログを確認して。"

    # 4. Embed作成
    embed = discord.Embed(title="🧾 レシート解析結果", color=discord.Color.green())
    total = 0
    for item in result.items:
        embed.add_field(
            name=f"{item.category}｜{item.name}",
            value=f"¥{item.price:,}　（{item.date}）",
            inline=False,
        )
        total += item.price
    embed.set_footer(text=f"合計: ¥{total:,}　全{len(result.items)}件{notion_status}")

    json_pretty = json.dumps(json.loads(result.raw_json), ensure_ascii=False, indent=2)

    # 5. 1回の edit でJSON + Embed + Notion結果をまとめて表示
    await msg.edit(
        content=f"```json\n{json_pretty[:1800]}\n```",
        embed=embed,
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BudgetCog(bot))
