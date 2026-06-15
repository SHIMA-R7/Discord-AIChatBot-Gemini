"""
Budget Cog — /budget コマンド

使い方:
  /budget とともにレシート画像を添付して送信
  → 圧縮 → Gemini解析 → Discord返信 → Notion登録
"""
from __future__ import annotations

import io
import json
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services import receipt_service, notion_service

logger = logging.getLogger(__name__)


class BudgetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="budget",
        description="レシート写真を解析して家計簿に追加する（画像を添付してね）",
    )
    async def budget(self, interaction: discord.Interaction) -> None:
        # 画像添付チェック
        images = [
            a for a in (interaction.message.attachments if interaction.message else [])
            if a.content_type and a.content_type.startswith("image/")
        ]

        # スラッシュコマンドでは attachments はinteraction側に来る
        # → discord.py 2.x では interaction に直接 attachmentsがないため
        #   「画像なし」のケースをここでガード
        await interaction.response.defer(thinking=True)

        # 添付画像を interaction から取るため、再取得
        data = await interaction.original_response()

        # --- 実際には /budget と画像は同時送信できないため on_message でも受け付ける ---
        # スラッシュコマンド単体では画像を受け取れない制約があるため
        # 「#会話チャンネルで !budget と画像を送る」方式も案内する
        await interaction.followup.send(
            "……`/budget` はスラッシュコマンドでは画像を受け取れない制約がある。\n"
            "**`#会話` チャンネルに `!budget` と書いて、レシート画像を一緒に添付して送って。**\n"
            "そっちで解析するから。",
            ephemeral=True,
        )

    # ─── !budget プレフィックスコマンド（画像添付対応）───────────────
    @commands.command(name="budget")
    async def budget_prefix(self, ctx: commands.Context) -> None:
        images = [a for a in ctx.message.attachments
                  if a.content_type and a.content_type.startswith("image/")]

        if not images:
            await ctx.reply(
                "……レシートの画像が添付されていない。画像と一緒に `!budget` を送って。",
                mention_author=False,
            )
            return

        # 複数枚来た場合は最初の1枚のみ処理
        attachment = images[0]
        msg = await ctx.reply("……解析中。少し待って。", mention_author=False)

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
            await msg.edit(
                content=f"……解析でエラーが出た。ごめん。\n```\n{result.error}\n```"
            )
            return

        if not result.items:
            await msg.edit(
                content="……レシートから品目を読み取れなかった。画像がぼけてたりしない？"
            )
            return

        # 3. Discord返信（見やすいEmbedで）
        embed = discord.Embed(
            title="🧾 レシート解析結果",
            color=discord.Color.green(),
        )
        total = 0
        for item in result.items:
            embed.add_field(
                name=f"{item.category}｜{item.name}",
                value=f"¥{item.price:,}　（{item.date}）",
                inline=False,
            )
            total += item.price

        embed.set_footer(text=f"合計: ¥{total:,}　全{len(result.items)}件")

        # 生のJSONもコードブロックで添付
        json_pretty = json.dumps(
            json.loads(result.raw_json), ensure_ascii=False, indent=2
        )
        await msg.edit(
            content=f"```json\n{json_pretty[:1800]}\n```",
            embed=embed,
        )

        # 4. Notionへ送信
        if not notion_service.NOTION_TOKEN or not notion_service.NOTION_DATABASE_ID:
            await ctx.send(
                "……Notionの設定がない。`.env` に `NOTION_TOKEN` と `NOTION_DATABASE_ID` を追加して。",
                reference=ctx.message,
            )
            return

        notion_items = [
            {"name": i.name, "price": i.price, "category": i.category, "date": i.date}
            for i in result.items
        ]
        ok, ng = await notion_service.add_items(notion_items)

        if ng == 0:
            await ctx.send(
                f"……Notionに **{ok}件** 登録した。べ、別に丁寧にやったわけじゃないし。",
                reference=ctx.message,
            )
        else:
            await ctx.send(
                f"……Notionに {ok}件登録できたけど、{ng}件は失敗した。ログを確認して。",
                reference=ctx.message,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BudgetCog(bot))
