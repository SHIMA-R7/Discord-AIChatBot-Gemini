"""
QA Cog — /q コマンド & #会話チャンネル自動返信
画像添付があれば image_understanding_service 経由でマルチモーダル処理。
"""
from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from services import gemini_service, system_log_service

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
        in_vc    = bool(member and member.voice and member.voice.channel)

        answer, thoughts, workspace_error = await _ask_with_workspace(guild_id, question, in_vc)

        if thoughts and interaction.guild:
            await system_log_service.post(self.bot, interaction.guild, thoughts=thoughts)

        reply = answer + workspace_error
        for chunk in _split_message(reply, MAX_REPLY_LEN):
            await interaction.followup.send(chunk)

        if in_vc:
            voice_cog = self.bot.cogs.get("VoiceCog")
            if voice_cog:
                await voice_cog.speak_in_channel(member.voice.channel, answer, interaction.guild)

    # ─── /qclear ─────────────────────────────────────────
    @app_commands.command(name="qclear", description="凛との会話履歴と記憶をすべてリセットする")
    async def clear(self, interaction: discord.Interaction) -> None:
        gemini_service.clear_history(interaction.guild_id or 0)
        await interaction.response.send_message(
            "……記憶を全部消した。短期も長期も。また一から教えてあげる。", ephemeral=True
        )

    # ─── /qstatus ────────────────────────────────────────
    @app_commands.command(name="qstatus", description="凛の記憶状況を確認する")
    async def status(self, interaction: discord.Interaction) -> None:
        st = gemini_service.get_memory_status(interaction.guild_id or 0)
        next_summary = gemini_service.SUMMARY_INTERVAL - (st["turns"] % gemini_service.SUMMARY_INTERVAL)
        lines = [
            f"**総会話ターン数:** {st['turns']}",
            f"**短期履歴:** {st['short_history']} ターン（最大 {config.MAX_HISTORY}）",
            f"**区間要約:** {st['recent_summaries']} / {gemini_service.MAX_RECENT_SUMMARY} 件",
            f"**長期記憶:** {'あり（' + str(st['long_summary_len']) + '文字）' if st['has_long_summary'] else 'なし'}",
            f"**次の要約まで:** あと {next_summary} ターン",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # ─── #会話 チャンネル（一本化）───────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.channel.name != config.CH_CHAT:
            return

        text = message.content.strip()
        images = [a for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")]

        # テキストも画像もない（スタンプ等）は無視
        if not text and not images:
            return

        # 画像生成リクエスト判定（テキストのみ・画像なし）
        if not images:
            from cogs.draw_cog import is_draw_request, do_generate_and_reply
            if is_draw_request(text):
                async with message.channel.typing():
                    prompt_en = await do_generate_and_reply(message)
                if prompt_en:
                    await system_log_service.post(self.bot, message.guild, prompt_en=prompt_en)
                return

        guild_id = message.guild.id
        member   = message.guild.get_member(message.author.id)
        in_vc    = bool(member and member.voice and member.voice.channel)

        async with message.channel.typing():
            if images:
                # 画像添付あり → マルチモーダル処理
                answer, thoughts = await _ask_with_image(
                    guild_id, text, images[0], in_vc
                )
                workspace_error = ""
            else:
                # テキストのみ → 通常会話
                answer, thoughts, workspace_error = await _ask_with_workspace(
                    guild_id, text, in_vc
                )

        if thoughts:
            await system_log_service.post(self.bot, message.guild, thoughts=thoughts)

        reply = answer + workspace_error
        for chunk in _split_message(reply, MAX_REPLY_LEN):
            await message.reply(chunk, mention_author=False)

        if in_vc:
            voice_cog = self.bot.cogs.get("VoiceCog")
            if voice_cog:
                await voice_cog.speak_in_channel(member.voice.channel, answer, message.guild)


# ─── 画像+テキスト処理 ───────────────────────────────────────
async def _ask_with_image(
    guild_id: int,
    text: str,
    attachment: discord.Attachment,
    voice_mode: bool,
) -> tuple[str, str]:
    from services.image_understanding_service import ask_with_image

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as resp:
                image_bytes = await resp.read()
    except Exception as e:
        logger.error(f"画像ダウンロード失敗: {e}")
        return f"……画像のダウンロードに失敗した。（{e}）", ""

    mime = attachment.content_type or "image/jpeg"
    return await ask_with_image(guild_id, text, image_bytes, mime, voice_mode)


# ─── 共通: Workspace取得 + Gemini呼び出し ────────────────────
async def _ask_with_workspace(
    guild_id: int, question: str, voice_mode: bool
) -> tuple[str, str, str]:
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
    answer, thoughts = await gemini_service.ask(
        guild_id=guild_id, user_message=full_question, voice_mode=voice_mode
    )
    return answer, thoughts, workspace_error


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
