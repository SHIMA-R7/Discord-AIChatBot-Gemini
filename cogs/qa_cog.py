"""
QA Cog — /q コマンド
- サーバー内のどのチャンネルからでも質問可能
- ボイスチャンネルにいる場合は voice_cog に音声出力を委譲
- Google Workspace (Gmail/Calendar/Drive) 連携機能を追加
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import gemini_service

logger = logging.getLogger(__name__)

# 応答の最大文字数（Discordの上限は2000）
MAX_REPLY_LEN = 1900


class QACog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─── /q コマンド ────────────────────────────────────
    @app_commands.command(
        name="q",
        description="凛に質問する（どのチャンネルからでもOK）"
    )
    @app_commands.describe(question="質問内容を入力してください")
    async def question(
        self,
        interaction: discord.Interaction,
        question: str,
    ) -> None:
        await interaction.response.defer(thinking=True)

        guild_id = interaction.guild_id or 0
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        in_vc = (
            member is not None
            and member.voice is not None
            and member.voice.channel is not None
        )

        # Workspace情報をコンテキストとして付与（キーワードがある場合のみ）
        context = ""
        q_lower = question.lower()
        try:
            from services import workspace_service
            
            # メール連携
            if any(k in q_lower for k in ["メール", "mail", "gmail", "受信"]):
                emails = await workspace_service.get_recent_emails(3)
                context += "\n【未読メール（直近3件）】\n"
                for e in emails:
                    context += f"- {e['date']} | {e['from']} | {e['subject']}\n  {e['snippet']}\n"

            # カレンダー連携
            if any(k in q_lower for k in ["予定", "カレンダー", "calendar", "スケジュール"]):
                events = await workspace_service.get_upcoming_events(5)
                context += "\n【直近の予定】\n"
                for e in events:
                    context += f"- {e['start']} | {e['summary']} {e['location']}\n"

            # ドライブ連携
            if any(k in q_lower for k in ["ドライブ", "drive", "ファイル", "file"]):
                # 質問からファイル名キーワードを抽出（簡易的に先頭20文字）
                files = await workspace_service.search_drive(question[:20], 3)
                context += "\n【Driveファイル検索結果】\n"
                for f in files:
                    context += f"- {f['name']} ({f['type']}) 更新: {f['modified']}\n  {f['link']}\n"

        except Exception as e:
            logger.warning(f"Workspace取得エラー（続行）: {e}")

        full_question = question
        if context:
            full_question = f"{question}\n\n{context}"

        # Gemini に問い合わせ
        answer = await gemini_service.ask(
            guild_id=guild_id,
            user_message=full_question,
            voice_mode=in_vc,
        )

        # テキスト返答（長い場合は分割）
        chunks = _split_message(answer, MAX_REPLY_LEN)
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk)

        # VCにいる場合、voice_cog に音声再生を依頼
        if in_vc:
            voice_cog: "VoiceCog | None" = self.bot.cogs.get("VoiceCog")  # type: ignore
            if voice_cog:
                await voice_cog.speak_in_channel(
                    member.voice.channel,
                    answer,
                    interaction.guild,
                )

    # ─── /qclear コマンド ────────────────────────────────
    @app_commands.command(
        name="qclear",
        description="凛との会話履歴をリセットする"
    )
    async def clear(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id or 0
        gemini_service.clear_history(guild_id)
        await interaction.response.send_message(
            "……記憶を消した。また一から教えてあげる。",
            ephemeral=True,
        )

    # ─── /qstatus コマンド ───────────────────────────────
    @app_commands.command(
        name="qstatus",
        description="凛の現在の会話ターン数を確認する"
    )
    async def status(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id or 0
        turns = gemini_service.get_history_len(guild_id)
        await interaction.response.send_message(
            # gemini_service.config.MAX_HISTORY へのアクセスを想定
            f"現在 **{turns}** ターン分の記憶がある。最大 **{gemini_service.config.MAX_HISTORY}** ターンまで。",
            ephemeral=True,
        )


def _split_message(text: str, limit: int) -> list[str]:
    """テキストをDiscord上限に合わせて分割"""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QACog(bot))# venv有効化した状態で