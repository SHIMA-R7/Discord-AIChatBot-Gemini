"""
QA Cog — /q コマンド & #会話チャンネル自動返信
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from services import gemini_service

logger = logging.getLogger(__name__)

MAX_REPLY_LEN = 1900


class QACog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─── /q コマンド ─────────────────────────────────────
    @app_commands.command(name="q", description="凛に質問する（どのチャンネルからでもOK）")
    @app_commands.describe(question="質問内容を入力してください")
    async def question(self, interaction: discord.Interaction, question: str) -> None:
        await interaction.response.defer(thinking=True)

        guild_id = interaction.guild_id or 0
        member   = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        in_vc    = member and member.voice and member.voice.channel is not None

        answer, workspace_error = await _ask_with_workspace(guild_id, question, bool(in_vc))

        reply  = answer + workspace_error
        chunks = _split_message(reply, MAX_REPLY_LEN)
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk)

        if in_vc:
            voice_cog = self.bot.cogs.get("VoiceCog")
            if voice_cog:
                await voice_cog.speak_in_channel(member.voice.channel, answer, interaction.guild)

    # ─── /qclear ─────────────────────────────────────────
    @app_commands.command(name="qclear", description="凛との会話履歴をリセットする")
    async def clear(self, interaction: discord.Interaction) -> None:
        gemini_service.clear_history(interaction.guild_id or 0)
        await interaction.response.send_message("……記憶を消した。また一から教えてあげる。", ephemeral=True)

    # ─── /qstatus ────────────────────────────────────────
    @app_commands.command(name="qstatus", description="凛の現在の会話ターン数を確認する")
    async def status(self, interaction: discord.Interaction) -> None:
        turns = gemini_service.get_history_len(interaction.guild_id or 0)
        await interaction.response.send_message(
            f"現在 **{turns}** ターン分の記憶がある。最大 **{gemini_service.config.MAX_HISTORY}** ターンまで。",
            ephemeral=True,
        )

    # ─── #会話 チャンネル自動返信 ────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.channel.name != config.CH_CHAT:
            return
        if not message.content.strip():
            return

        guild_id = message.guild.id
        member   = message.guild.get_member(message.author.id)
        in_vc    = member and member.voice and member.voice.channel is not None

        async with message.channel.typing():
            answer, workspace_error = await _ask_with_workspace(guild_id, message.content, bool(in_vc))

        reply  = answer + workspace_error
        chunks = _split_message(reply, MAX_REPLY_LEN)
        for chunk in chunks:
            await message.reply(chunk, mention_author=False)

        if in_vc:
            voice_cog = self.bot.cogs.get("VoiceCog")
            if voice_cog:
                await voice_cog.speak_in_channel(member.voice.channel, answer, message.guild)


# ─── 共通: Workspace取得 + Gemini呼び出し ────────────────────
async def _ask_with_workspace(
    guild_id: int, question: str, voice_mode: bool
) -> tuple[str, str]:
    context         = ""
    workspace_error = ""
    q_lower         = question.lower()

    try:
        from services import workspace_service

        if any(k in q_lower for k in ["メール", "mail", "gmail", "受信"]):
            emails = await workspace_service.get_recent_emails(3)
            context += "\n【未読メール（直近3件）】\n"
            for e in emails:
                context += f"- {e['date']} | {e['from']} | {e['subject']}\n  {e['snippet']}\n"

        if any(k in q_lower for k in ["予定", "カレンダー", "calendar", "スケジュール"]):
            events = await workspace_service.get_upcoming_events(5)
            context += "\n【直近の予定】\n"
            for e in events:
                context += f"- {e['start']} | {e['summary']} {e['location']}\n"

        if any(k in q_lower for k in ["ドライブ", "drive", "ファイル", "file"]):
            files = await workspace_service.search_drive(question[:20], 3)
            context += "\n【Driveファイル検索結果】\n"
            for f in files:
                context += f"- {f['name']} ({f['type']}) 更新: {f['modified']}\n  {f['link']}\n"

    except RuntimeError as e:
        workspace_error = f"\n> ⚠️ Workspace連携エラー: {e}"
        logger.warning(f"Workspace設定エラー: {e}")
    except Exception as e:
        workspace_error = f"\n> ⚠️ Workspace取得に失敗した（{type(e).__name__}: {e}）"
        logger.warning(f"Workspace取得エラー: {e}")

    full_question = f"{question}\n\n{context}" if context else question
    answer = await gemini_service.ask(guild_id=guild_id, user_message=full_question, voice_mode=voice_mode)
    return answer, workspace_error


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QACog(bot))
