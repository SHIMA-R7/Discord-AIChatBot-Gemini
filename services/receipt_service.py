"""
レシート解析サービス

処理フロー:
  1. 画像をリサイズ（長辺1200px・JPEG quality=75 に圧縮）
  2. Gemini Flash に投げてJSON抽出
  3. 結果を ReceiptResult として返す
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from google import genai
from google.genai import types
from PIL import Image

import config

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)

# レシート解析用は Flash-8B（最軽量・安価）
RECEIPT_MODEL = "gemini-2.0-flash-lite"

# リサイズ上限（長辺px）・JPEG品質
MAX_LONG_SIDE = 1200
JPEG_QUALITY  = 75


@dataclass
class ReceiptItem:
    name:     str
    price:    int
    category: str
    date:     str  # YYYY-MM-DD


@dataclass
class ReceiptResult:
    items:     list[ReceiptItem] = field(default_factory=list)
    raw_json:  str = ""
    error:     str = ""


def _compress_image(image_bytes: bytes) -> bytes:
    """長辺1200px・JPEG75に圧縮して返す"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    long_side = max(w, h)
    if long_side > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / long_side
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    compressed = buf.getvalue()
    logger.info(
        f"画像圧縮: {len(image_bytes)//1024}KB → {len(compressed)//1024}KB "
        f"({img.size[0]}x{img.size[1]}px)"
    )
    return compressed


_PROMPT = """\
このレシート画像から品目情報を読み取り、以下のJSON形式だけを返してください。
余分なテキスト・コードブロック記号(```)は不要です。JSONのみ出力してください。

{
  "date": "YYYY-MM-DD",   // レシートの日付。不明なら今日の日付
  "items": [
    {
      "name": "品名",
      "price": 金額（整数・税込）,
      "category": "カテゴリ"
    }
  ]
}

カテゴリは以下から最も近いものを選んでください:
食費, 日用品, 外食, 交通費, 医療・薬, 衣類, 趣味・娯楽, 電子機器, 美容・健康, その他
"""


async def analyze(image_bytes: bytes) -> ReceiptResult:
    """レシート画像を解析してReceiptResultを返す"""
    def _run() -> ReceiptResult:
        compressed = _compress_image(image_bytes)

        response = _client.models.generate_content(
            model=RECEIPT_MODEL,
            contents=[
                types.Part.from_bytes(data=compressed, mime_type="image/jpeg"),
                types.Part.from_text(text=_PROMPT),
            ],
        )
        raw = (response.text or "").strip()
        # コードブロックが混入しても除去
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip()

        try:
            data    = json.loads(raw)
            receipt_date = data.get("date") or date.today().isoformat()
            items = [
                ReceiptItem(
                    name=i.get("name", "不明"),
                    price=int(i.get("price", 0)),
                    category=i.get("category", "その他"),
                    date=receipt_date,
                )
                for i in data.get("items", [])
            ]
            return ReceiptResult(items=items, raw_json=raw)
        except json.JSONDecodeError as e:
            logger.error(f"JSONパース失敗: {e}\nraw={raw}")
            return ReceiptResult(error=f"JSONパース失敗: {e}", raw_json=raw)

    return await asyncio.to_thread(_run)
