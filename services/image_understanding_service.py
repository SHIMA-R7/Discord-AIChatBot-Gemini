"""
画像理解サービス（#会話チャンネル用）

- 添付画像を取得し、IMAGE_MAX_BYTES を超えていれば自動圧縮
- Gemini のマルチモーダル入力で画像+テキストを送信して回答を得る
"""
from __future__ import annotations

import asyncio
import io
import logging

from PIL import Image
from google import genai
from google.genai import types

import config
import services.gemini_service as gs  # 内部ストアを共有

logger = logging.getLogger(__name__)


def _compress_if_needed(image_bytes: bytes) -> tuple[bytes, bool]:
    """
    IMAGE_MAX_BYTES を超えていれば JPEG 圧縮して返す。
    returns: (bytes, was_compressed)
    """
    if len(image_bytes) <= config.IMAGE_MAX_BYTES:
        return image_bytes, False

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size

    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= config.IMAGE_MAX_BYTES:
            logger.info(
                f"画像圧縮: {len(image_bytes)//1024}KB → {len(data)//1024}KB "
                f"(quality={quality}, {w}x{h}px)"
            )
            return data, True

    # まだ大きければリサイズ
    img = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=55, optimize=True)
    data = buf.getvalue()
    logger.info(
        f"画像リサイズ+圧縮: {len(image_bytes)//1024}KB → {len(data)//1024}KB "
        f"({img.size[0]}x{img.size[1]}px)"
    )
    return data, True


async def ask_with_image(
    guild_id: int,
    text: str,
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    voice_mode: bool = False,
) -> tuple[str, str]:
    """
    画像+テキストを Gemini に送って (answer, thoughts) を返す。
    会話履歴にはテキスト表現で記録（画像バイトは保持しない）。
    """
    data, was_compressed = _compress_if_needed(image_bytes)
    actual_mime = "image/jpeg" if was_compressed else mime_type

    try:
        cfg = gs._make_config(guild_id, voice_mode)

        # 過去の会話履歴 + 今回の画像メッセージ
        all_contents = list(gs._histories[guild_id]) + [
            types.Content(role="user", parts=[
                types.Part.from_bytes(data=data, mime_type=actual_mime),
                types.Part.from_text(text=text or "この画像について教えて。"),
            ])
        ]

        response = await gs._client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=all_contents,
            config=cfg,
        )
        raw    = response.text or "（応答が空だった……）"
        answer, thoughts = gs._strip_thoughts(raw)

        # 履歴にはテキストで記録
        label = f"[画像: {len(data)//1024}KB{'・圧縮済' if was_compressed else ''}]"
        gs._histories[guild_id].append(
            types.Content(role="user",  parts=[types.Part(text=f"{label} {text}")])
        )
        gs._histories[guild_id].append(
            types.Content(role="model", parts=[types.Part(text=answer)])
        )
        gs._turn_count[guild_id] += 1

        # 短期履歴トリム
        h = gs._histories[guild_id]
        while len(h) > config.MAX_HISTORY * 2:
            h.pop(0)
            h.pop(0)

        asyncio.create_task(gs._maybe_summarize(guild_id))
        return answer, thoughts

    except Exception as e:
        logger.error(f"画像理解エラー (guild={guild_id}): {e}", exc_info=True)
        return f"……画像の解析でエラーが出た。（{type(e).__name__}）", ""
