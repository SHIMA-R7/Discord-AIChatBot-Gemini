"""
Voice Cog — VOICEVOX読み上げ専用版
音声受信(VCマイク入力)は削除。/q コマンドからの読み上げのみ担当。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import discord
from discord import app_commands
from discord.ext import commands

from services import voicevox_service

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self._queues: dict[int, asyncio.Queue] = {}
        self._tasks:  dict[int, asyncio.Task]  = {}

    # ─── /vjoin ──────────────────────────────────────────────────
    @app_commands.command(name="vjoin", description="凛をVCに呼ぶ（読み上げモード）")
    async def vjoin(self, interaction: discord.Interaction) -> None:
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "……あなたがVCにいないと参加できない。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await self._join(member.voice.channel, interaction.guild)
        await interaction.followup.send(
            f"**{member.voice.channel.name}** に入った。`/q` で話しかけてみて。",
            ephemeral=True,
        )

    # ─── /vleave ─────────────────────────────────────────────────
    @app_commands.command(name="vleave", description="凛をVCから退出させる")
    async def vleave(self, interaction: discord.Interaction) -> None:
        guild     = interaction.guild
        vc_client = guild.voice_client if guild else None
        if vc_client and vc_client.is_connected():
            await vc_client.disconnect()
            self._stop_queue(guild.id)
            await interaction.response.send_message(
                "……退出した。また呼んで。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "今どこのVCにもいない。", ephemeral=True
            )

    # ─── /q から呼ばれる読み上げ ─────────────────────────────────
    async def speak_in_channel(
        self,
        channel: discord.VoiceChannel,
        text: str,
        guild: discord.Guild,
    ) -> None:
        await self._join(channel, guild)
        await self._enqueue(guild.id, guild, text)

    # ─── VC接続 ──────────────────────────────────────────────────
    async def _join(
        self,
        channel: discord.VoiceChannel,
        guild: discord.Guild,
    ) -> None:
        vc_client = guild.voice_client
        if vc_client:
            if vc_client.channel != channel:
                await vc_client.move_to(channel)
        else:
            await channel.connect()

        gid = guild.id
        if gid not in self._queues:
            self._queues[gid] = asyncio.Queue()
        if gid not in self._tasks or self._tasks[gid].done():
            self._tasks[gid] = asyncio.create_task(self._queue_worker(guild))

    # ─── 読み上げキュー ──────────────────────────────────────────
    async def _enqueue(self, guild_id: int, guild: discord.Guild, text: str) -> None:
        if guild_id not in self._queues:
            self._queues[guild_id] = asyncio.Queue()
        await self._queues[guild_id].put(text)

    async def _queue_worker(self, guild: discord.Guild) -> None:
        gid   = guild.id
        queue = self._queues[gid]
        while True:
            try:
                text = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                logger.info(f"読み上げキューアイドルタイムアウト (guild={gid})")
                break
            vc_client = guild.voice_client
            if not vc_client or not vc_client.is_connected():
                queue.task_done()
                break
            wav = await voicevox_service.synthesize(text)
            if wav:
                await self._play_wav(vc_client, wav)
            queue.task_done()

    async def _play_wav(self, vc_client: discord.VoiceClient, wav_bytes: bytes) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp_path = f.name
        done = asyncio.Event()
        def after(err):
            if err:
                logger.error(f"再生エラー: {err}")
            done.set()
        try:
            vc_client.play(discord.FFmpegPCMAudio(tmp_path), after=after)
            await done.wait()
        finally:
            os.unlink(tmp_path)

    def _stop_queue(self, guild_id: int) -> None:
        t = self._tasks.pop(guild_id, None)
        if t and not t.done():
            t.cancel()
        self._queues.pop(guild_id, None)

    # ─── 全員退出で自動退室 ──────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        guild     = member.guild
        vc_client = guild.voice_client
        if not vc_client or not vc_client.is_connected():
            return
        remaining = [m for m in vc_client.channel.members if not m.bot]
        if not remaining:
            await vc_client.disconnect()
            self._stop_queue(guild.id)
            logger.info(f"全員退出のため自動退室 (guild={guild.id})")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))
