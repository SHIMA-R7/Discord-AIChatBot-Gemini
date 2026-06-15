"""
Budget Cog — レシート解析 & Notion家計簿登録

・!budget コマンド（画像添付）
・#家計簿 チャンネルへの画像投稿で自動実行
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

    # ─── /budget（案内のみ） ──────────────────────────────
    @app_commands.command(name="budget", description="レシート家計簿の使い方を表示する")
    async def budget_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"**レシート解析の使い方**\n"
            f"① `#{config.CH_BUDGET}` チャンネルにレシート画像を投稿する（自動解析）\n"
            f"② どこのチャンネルでも `!budget` と書いて画像を添付して送る",
            ephemeral=True,
        )

    # ─── !budget プレフィックスコマンド ──────────────────
    @commands.command(name="budget")
    async def budget_prefix(self, ctx: commands.Context) -> None:
        images = [a for a in ctx.message.attachments
                  if a.content_type and a.content_type.startswith("image/")]
        if not images:
            await ctx.reply("……レシートの画像が添付されていない。画像と一緒に `!budget` を送って。", mention_author=False)
            return
        await _process_receipt(ctx.message, images[0])

    # ─── #家計簿 チャンネルへの画像投稿を自動処理 ────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.channel.name != config.CH_BUDGET:
            return

        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return  # テキストだけの投稿は無視

        await _process_receipt(message, images[0])


# ─── 共通処理 ─────────────────────────────────────────────────
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

    # 3. Discord返信（Embed）
    embed = discord.Embed(title="🧾 レシート解析結果", color=discord.Color.green())
    total = 0
    for item in result.items:
        embed.add_field(
            name=f"{item.category}｜{item.name}",
            value=f"¥{item.price:,}　（{item.date}）",
            inline=False,
        )
        total += item.price
    embed.set_footer(text=f"合計: ¥{total:,}　全{len(result.items)}件")

    json_pretty = json.dumps(json.loads(result.raw_json), ensure_ascii=False, indent=2)
    await msg.edit(content=f"```json\n{json_pretty[:1800]}\n```", embed=embed)

    # 4. Notion登録
    if not notion_service.NOTION_TOKEN or not notion_service.NOTION_DATABASE_ID:
        await message.reply(
            "……Notionの設定がない。`.env` に `NOTION_TOKEN` と `NOTION_DATABASE_ID` を追加して。",
            mention_author=False,
        )
        return

    notion_items = [
        {"name": i.name, "price": i.price, "category": i.category, "date": i.date}
        for i in result.items
    ]
    ok, ng = await notion_service.add_items(notion_items)

    if ng == 0:
        await message.reply(f"……Notionに **{ok}件** 登録した。べ、別に丁寧にやったわけじゃないし。", mention_author=False)
    else:
        await message.reply(f"……Notionに {ok}件登録できたけど、{ng}件は失敗した。ログを確認して。", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BudgetCog(bot))
