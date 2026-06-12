"""
Voice Cog — 音声入力 + VOICEVOX読み上げ

Dave E2EE 対応版:
- discord.py の vc_client.ws.dave_session を使って第2段階復号
- speaking_stop イベントで発話終了を検出

【修正】
- Opus decode の "corrupted stream" は接続直後の数パケットに必ず発生する
  Discord DAVE (E2E暗号化) の初期化遅延が原因で、ライブラリの既知の挙動。
  大量の DEBUG ログを抑制するため、連続エラーカウンタを導入。
  接続後 OPUS_ERROR_SUPPRESS_COUNT パケット分はエラーを無視する。
- asyncio.get_event_loop() は Python 3.10+ で非推奨のため
  asyncio.get_running_loop() に変更。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import tempfile
import time
import wave
from collections import defaultdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, voice_recv

#import davey

from services import gemini_service, voicevox_service, whisper_service

logger = logging.getLogger(__name__)

# ── パラメータ ────────────────────────────────────────────────────
SILENCE_THRESHOLD  = 200   # RMS閾値
SILENCE_DURATION   = 0.5   # 秒: タイムアウト
MIN_SPEECH_SECONDS = 0.3   # 秒: これ未満の録音は無視
SAMPLE_RATE        = 48000
CHANNELS           = 2
SAMPLE_WIDTH       = 2     # 16bit PCM

# 【修正】接続直後のOpusエラーを抑制するパケット数
# DAVE セッション初期化が完了するまでの間、corrupted stream が大量発生する。
# この値を超えたエラーは WARNING として記録する。
OPUS_ERROR_SUPPRESS_COUNT = 50


def _calc_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    n = len(pcm) // 2
    samples = struct.unpack_from(f"<{n}h", pcm)
    return (sum(s * s for s in samples) / n) ** 0.5


def _pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


# ── ユーザーごとのOpusデコーダー ─────────────────────────────────
_decoders: dict[int, discord.opus.Decoder] = {}

def _get_decoder(user_id: int) -> discord.opus.Decoder:
    if user_id not in _decoders:
        _decoders[user_id] = discord.opus.Decoder()
    return _decoders[user_id]


# ── 発話バッファ ─────────────────────────────────────────────────
class SpeechBuffer:
    def __init__(self):
        self.pcm_buf:     bytearray = bytearray()
        self.last_voice:  float     = 0.0
        self.is_speaking: bool      = False

    def feed(self, pcm: bytes) -> Optional[bytes]:
        rms = _calc_rms(pcm)
        now = time.monotonic()
        if rms > SILENCE_THRESHOLD:
            self.pcm_buf    += pcm
            self.last_voice  = now
            self.is_speaking = True
            return None
        if not self.is_speaking:
            return None
        if now - self.last_voice < SILENCE_DURATION:
            return None
        # 発話終了
        data = bytes(self.pcm_buf)
        self.pcm_buf      = bytearray()
        self.is_speaking  = False
        self.last_voice   = 0.0
        duration = len(data) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
        if duration < MIN_SPEECH_SECONDS:
            return None
        return _pcm_to_wav(data)

    def flush(self) -> Optional[bytes]:
        """speaking_stop イベントで即座に取り出す"""
        if not self.is_speaking or not self.pcm_buf:
            return None
        data = bytes(self.pcm_buf)
        self.pcm_buf      = bytearray()
        self.is_speaking  = False
        self.last_voice   = 0.0
        duration = len(data) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
        if duration < MIN_SPEECH_SECONDS:
            return None
        return _pcm_to_wav(data)


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        # 【修正】get_event_loop() → get_running_loop()（Python 3.10+ 非推奨対応）
        self._loop    = asyncio.get_running_loop()
        self._queues:  dict[int, asyncio.Queue] = {}
        self._tasks:   dict[int, asyncio.Task]  = {}
        self._buffers: dict[int, SpeechBuffer]  = defaultdict(SpeechBuffer)
        self._busy:    dict[int, bool]           = defaultdict(bool)
        self._guilds:  dict[int, discord.Guild]  = {}
        # ssrc → user_id のマッピング（Dave復号に必要）
        self._ssrc_to_uid: dict[int, int]        = {}
        # 【修正】ユーザーごとのOpusエラーカウンタ（ノイズ抑制用）
        self._opus_err_count: dict[int, int]     = defaultdict(int)

    # ─── /vjoin ─────────────────────────────────────────────────
    @app_commands.command(name="vjoin", description="凛をVCに呼ぶ（音声会話モード）")
    async def vjoin(self, interaction: discord.Interaction) -> None:
        member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "……あなたがVCにいないと参加できない。", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        vc = member.voice.channel
        await self._join_and_listen(vc, interaction.guild)
        await interaction.followup.send(
            f"**{vc.name}** に入った。話しかけてみて。", ephemeral=True
        )

    # ─── /vleave ────────────────────────────────────────────────
    @app_commands.command(name="vleave", description="凛をVCから退出させる")
    async def vleave(self, interaction: discord.Interaction) -> None:
        guild     = interaction.guild
        vc_client = guild.voice_client if guild else None
        if vc_client and vc_client.is_connected():
            if isinstance(vc_client, voice_recv.VoiceRecvClient):
                vc_client.stop_listening()
            await vc_client.disconnect()
            self._stop_queue(guild.id)
            await interaction.response.send_message(
                "……退出した。また呼んで。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "今どこのVCにもいない。", ephemeral=True
            )

    # ─── /q から呼ばれる読み上げ ────────────────────────────────
    async def speak_in_channel(
        self,
        channel: discord.VoiceChannel,
        text: str,
        guild: discord.Guild,
    ) -> None:
        await self._join_and_listen(channel, guild)
        await self._enqueue(guild.id, text)

    # ─── VC接続 + 録音開始 ──────────────────────────────────────
    async def _join_and_listen(
        self,
        channel: discord.VoiceChannel,
        guild: discord.Guild,
    ) -> None:
        vc_client = guild.voice_client
        if vc_client:
            if isinstance(vc_client, voice_recv.VoiceRecvClient):
                vc_client.stop_listening()
            if vc_client.channel != channel:
                await vc_client.move_to(channel)
        else:
            vc_client = await channel.connect(cls=voice_recv.VoiceRecvClient)

        self._guilds[guild.id] = guild

        # 【修正】新規接続時にOpusエラーカウンタをリセット
        self._opus_err_count.clear()

        # Dave E2EE セッションを取得
        ws = getattr(vc_client, 'ws', None)
        dave_session = getattr(ws, 'dave_session', None) if ws else None
        if dave_session:
            logger.info(f"Dave E2EE セッション確認: ready={dave_session.ready}")
        else:
            logger.warning("Dave セッションが見つかりません")

        # decode=False で生パケットを受け取り自前で復号
        sink = voice_recv.BasicSink(self._on_audio_packet, decode=False)
        vc_client.listen(sink)
        logger.info(f"音声録音開始: {channel.name}")

        gid = guild.id
        if gid not in self._queues:
            self._queues[gid] = asyncio.Queue()
        if gid not in self._tasks or self._tasks[gid].done():
            self._tasks[gid] = asyncio.create_task(self._queue_worker(guild))

    # ─── ssrc → user_id マッピング更新（speaking イベントから）──
    @commands.Cog.listener()
    async def on_voice_member_speaking_state(
        self,
        member: discord.Member,
        ssrc: int,
        speaking: bool,
    ) -> None:
        if not member.bot:
            self._ssrc_to_uid[ssrc] = member.id

    # ─── 音声パケットコールバック（同期・別スレッドから呼ばれる）──
    def _on_audio_packet(
        self,
        user: Optional[discord.User],
        data: voice_recv.VoiceData,
    ) -> None:
        if user is None or user.bot:
            return

        guild_id = next(iter(self._guilds), None)
        if guild_id is None or self._busy[guild_id]:
            return

        if data.packet.is_silence():
            return

        # Step 1: 外側の暗号（aead_xchacha20）は voice_recv が解いてくれている
        outer_decrypted = data.packet.decrypted_data
        if not outer_decrypted:
            return

        # Step 2: Dave E2EE の内側の暗号を davey で復号
        guild = self._guilds.get(guild_id)
        if guild is None:
            return

        vc_client = guild.voice_client
        ws = getattr(vc_client, 'ws', None)
        dave_session = getattr(ws, 'dave_session', None) if ws else None

        opus_data = outer_decrypted
        if dave_session and dave_session.ready:
            try:
                opus_data = dave_session.decrypt(
                    user.id,
                    davey.MediaType.audio,
                    outer_decrypted,
                )
            except Exception as e:
                logger.debug(f"Dave decrypt skip (uid={user.id}): {e}")
                return

        # Step 3: Opus → PCM デコード
        try:
            pcm = _get_decoder(user.id).decode(opus_data, fec=False)
        except discord.opus.OpusError as e:
            # 【修正】接続直後の大量エラーを抑制
            # DAVE セッション初期化中は corrupted stream が頻発するが正常な挙動。
            # 一定数を超えてもエラーが続く場合のみ WARNING として出力する。
            count = self._opus_err_count[user.id] + 1
            self._opus_err_count[user.id] = count
            if count <= OPUS_ERROR_SUPPRESS_COUNT:
                logger.debug(f"Opus decode fail (uid={user.id}): {e} [{count}/{OPUS_ERROR_SUPPRESS_COUNT}]")
            elif count % 20 == 0:
                # 抑制後も続くなら20パケットごとに1回だけ警告
                logger.warning(f"Opus decode 持続エラー (uid={user.id}): {e} (累計 {count} 回)")
            return

        # デコード成功したらエラーカウンタをリセット
        self._opus_err_count[user.id] = 0

        if not pcm:
            return

        # Step 4: 音量チェック → バッファへ
        wav = self._buffers[user.id].feed(pcm)
        if wav is not None:
            asyncio.run_coroutine_threadsafe(
                self._process_speech(user, wav, guild_id),
                self._loop,
            )

    # ─── speaking_stop で即座に発話終了 ─────────────────────────
    @commands.Cog.listener()
    async def on_voice_member_speaking_stop(
        self,
        member: discord.Member,
        ssrc: int,
    ) -> None:
        guild_id = next(iter(self._guilds), None)
        if guild_id is None or self._busy[guild_id]:
            return

        buf = self._buffers.get(member.id)
        if buf is None:
            return

        wav = buf.flush()
        if wav is not None:
            asyncio.ensure_future(
                self._process_speech(member, wav, guild_id)
            )

    # ─── 発話完了後の処理（非同期）──────────────────────────────
    async def _process_speech(
        self,
        user: discord.User,
        wav_bytes: bytes,
        guild_id: int,
    ) -> None:
        self._busy[guild_id] = True
        try:
            guild = self._guilds.get(guild_id)
            if guild is None:
                return

            logger.info(f"発話検出: {user.display_name} ({len(wav_bytes)//1000}KB)")
            text = await whisper_service.transcribe(wav_bytes)

            if not text or "文字起こし" in text or len(text) < 2:
                logger.info(f"文字起こし無効、スキップ: 「{text}」")
                return

            logger.info(f"文字起こし: 「{text}」")
            await self._post_to_text_channel(guild, user, text[:1900])

            answer = await gemini_service.ask(
                guild_id=guild_id,
                user_message=text,
                voice_mode=True,
            )

            await self._post_to_text_channel(guild, None, answer[:1900])
            await self._enqueue(guild_id, answer)

        except Exception as e:
            logger.error(f"音声処理エラー: {e}", exc_info=True)
        finally:
            self._busy[guild_id] = False

    # ─── テキストチャンネルに投稿 ────────────────────────────────
    async def _post_to_text_channel(
        self,
        guild: discord.Guild,
        user: Optional[discord.User],
        text: str,
    ) -> None:
        ch = guild.system_channel or next(
            (c for c in guild.text_channels
             if c.permissions_for(guild.me).send_messages),
            None,
        )
        if ch is None:
            return
        if user:
            await ch.send(f"🎙️ **{user.display_name}**: {text}")
        else:
            await ch.send(f"🤖 **凛**: {text}")

    # ─── 読み上げキュー ──────────────────────────────────────────
    async def _enqueue(self, guild_id: int, text: str) -> None:
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
                logger.info(f"キューアイドルタイムアウト (guild={gid})")
                break
            vc_client = guild.voice_client
            if not vc_client or not vc_client.is_connected():
                queue.task_done()
                break
            wav = await voicevox_service.synthesize(text)
            if wav:
                await self._play_wav(vc_client, wav)
            queue.task_done()

    async def _play_wav(
        self, vc_client: discord.VoiceClient, wav_bytes: bytes
    ) -> None:
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
        self._guilds.pop(guild_id, None)
        self._busy.pop(guild_id, None)

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
            if isinstance(vc_client, voice_recv.VoiceRecvClient):
                vc_client.stop_listening()
            await vc_client.disconnect()
            self._stop_queue(guild.id)
            logger.info(f"全員退出のため自動退室 (guild={guild.id})")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))
