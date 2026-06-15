"""
Notion 家計簿サービス

【初回セットアップ】
  1. Notion でインテグレーションを作成 → NOTION_TOKEN を .env に設定
  2. 家計簿データベースを作成（または既存のものをインテグレーションに共有）
  3. NOTION_DATABASE_ID を .env に設定

データベースの列構成（このサービスが期待するもの）:
  品名      : title
  金額      : number
  カテゴリ  : select
  日付      : date
  登録元    : select  ← "Discord Bot" 固定
"""
from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

NOTION_TOKEN       = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type":  "application/json",
    "Notion-Version": "2022-06-28",
}


async def add_item(name: str, price: int, category: str, date: str) -> bool:
    """
    Notionデータベースに1件追加。
    成功→True / 失敗→False
    """
    if not NOTION_TOKEN or not NOTION_DATABASE_ID:
        logger.warning("NOTION_TOKEN または NOTION_DATABASE_ID が未設定")
        return False

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "品名": {
                "title": [{"text": {"content": name}}]
            },
            "金額": {
                "number": price
            },
            "カテゴリ": {
                "select": {"name": category}
            },
            "日付": {
                "date": {"start": date}
            },
            "登録元": {
                "select": {"name": "Discord Bot"}
            },
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.notion.com/v1/pages",
                headers=_HEADERS,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                logger.error(f"Notion APIエラー {resp.status}: {body}")
                return False
    except Exception as e:
        logger.error(f"Notion通信エラー: {e}", exc_info=True)
        return False


async def add_items(items: list[dict]) -> tuple[int, int]:
    """
    複数件を並列追加。
    items: [{"name":..., "price":..., "category":..., "date":...}, ...]
    returns: (成功件数, 失敗件数)
    """
    results = await asyncio.gather(
        *[add_item(**i) for i in items],
        return_exceptions=False,
    )
    ok  = sum(1 for r in results if r)
    ng  = len(results) - ok
    return ok, ng
