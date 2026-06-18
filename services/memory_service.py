"""
長期記憶サービス（RAGベース）

アーキテクチャ:
  - SQLite + sqlite-vec でベクトル検索
  - 会話ターンごとに Gemini Embedding（768次元）を生成して保存
  - 質問時に類似度上位 TOP_K 件の記憶を取得してプロンプトに注入
  - 再起動しても記憶が消えない永続化

DBスキーマ:
  memories テーブル : id, guild_id, role, content, timestamp
  vec_memories      : sqlite-vec の仮想テーブル（ベクトル検索用）
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import struct
import time
from pathlib import Path

import sqlite_vec
from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)

EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_DIM   = 768
TOP_K           = int(getattr(config, "MEMORY_TOP_K", 5))
DB_PATH         = Path(getattr(config, "MEMORY_DB_PATH", "memory.db"))


# ── DB初期化 ─────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_db() -> None:
    """起動時に1回呼ぶ"""
    conn = _get_conn()
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS memories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  INTEGER NOT NULL,
            role      TEXT    NOT NULL,  -- 'user' or 'model'
            content   TEXT    NOT NULL,
            timestamp REAL    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_guild ON memories(guild_id);

        CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
            memory_id INTEGER PRIMARY KEY,
            embedding FLOAT[{EMBEDDING_DIM}]
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"memory DB 初期化完了: {DB_PATH}")


# ── Embedding生成 ────────────────────────────────────────────────

async def _embed(text: str) -> list[float]:
    """テキストをEmbeddingベクトルに変換"""
    response = await _client.aio.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return response.embeddings[0].values


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ── 記憶の保存 ───────────────────────────────────────────────────

async def save_turn(guild_id: int, user_text: str, model_text: str) -> None:
    """
    1ターン（user + model）を非同期で保存。
    Embeddingは user+model を結合したテキストで1件生成。
    """
    combined = f"ユーザー: {user_text}\n凛: {model_text}"
    try:
        vec = await _embed(combined)
    except Exception as e:
        logger.warning(f"Embedding生成失敗（保存スキップ）: {e}")
        return

    def _insert():
        conn = _get_conn()
        ts   = time.time()
        # userとmodelを個別に保存（検索結果表示用）
        cur = conn.execute(
            "INSERT INTO memories (guild_id, role, content, timestamp) VALUES (?,?,?,?)",
            (guild_id, "user", user_text, ts),
        )
        user_id = cur.lastrowid
        conn.execute(
            "INSERT INTO memories (guild_id, role, content, timestamp) VALUES (?,?,?,?)",
            (guild_id, "model", model_text, ts),
        )
        # ベクトルはターン代表としてuser_idに紐付け
        conn.execute(
            "INSERT OR REPLACE INTO vec_memories (memory_id, embedding) VALUES (?,?)",
            (user_id, _pack(vec)),
        )
        conn.commit()
        conn.close()

    await asyncio.to_thread(_insert)
    logger.debug(f"記憶保存完了 (guild={guild_id})")


# ── 記憶の検索 ───────────────────────────────────────────────────

async def retrieve(guild_id: int, query: str) -> str:
    """
    queryに関連する記憶を検索して、プロンプトに注入するテキストブロックを返す。
    関連記憶がなければ空文字を返す。
    """
    try:
        vec = await _embed(query)
    except Exception as e:
        logger.warning(f"クエリEmbedding失敗: {e}")
        return ""

    def _search() -> list[tuple[int, float]]:
        conn = _get_conn()
        rows = conn.execute(
            f"""
            SELECT memory_id, distance
            FROM vec_memories
            WHERE embedding MATCH ?
              AND k = {TOP_K}
            ORDER BY distance
            """,
            (_pack(vec),),
        ).fetchall()
        conn.close()
        return rows

    try:
        hits = await asyncio.to_thread(_search)
    except Exception as e:
        logger.warning(f"ベクトル検索失敗: {e}")
        return ""

    if not hits:
        return ""

    # memory_id（user行）とその次のmodel行を取得
    memory_ids = [h[0] for h in hits]
    placeholders = ",".join("?" * len(memory_ids))

    def _fetch(ids: list[int]) -> list[tuple]:
        conn = _get_conn()
        # user行とその直後のmodel行（id+1）をまとめて取得
        all_ids = []
        for i in ids:
            all_ids.extend([i, i + 1])
        ph = ",".join("?" * len(all_ids))
        rows = conn.execute(
            f"SELECT id, role, content, timestamp FROM memories "
            f"WHERE id IN ({ph}) AND guild_id = ? ORDER BY timestamp",
            (*all_ids, guild_id),
        ).fetchall()
        conn.close()
        return rows

    rows = await asyncio.to_thread(_fetch, memory_ids)

    # ターンごとにペアを組む
    by_id = {r[0]: r for r in rows}
    blocks = []
    for uid in memory_ids:
        u = by_id.get(uid)
        m = by_id.get(uid + 1)
        if u and m:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(u[3]))
            blocks.append(f"[{ts}]\nユーザー: {u[2]}\n凛: {m[2]}")

    if not blocks:
        return ""

    result = "\n\n".join(blocks)
    logger.info(f"関連記憶 {len(blocks)}件取得 (guild={guild_id})")
    return result


# ── リセット ─────────────────────────────────────────────────────

async def clear(guild_id: int) -> None:
    def _clear():
        conn = _get_conn()
        # vec_memoriesから対象guild_idのmemory_idを特定して削除
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM memories WHERE guild_id=? AND role='user'", (guild_id,)
        ).fetchall()]
        if ids:
            ph = ",".join("?" * len(ids))
            conn.execute(f"DELETE FROM vec_memories WHERE memory_id IN ({ph})", ids)
        conn.execute("DELETE FROM memories WHERE guild_id=?", (guild_id,))
        conn.commit()
        conn.close()
    await asyncio.to_thread(_clear)
    logger.info(f"記憶削除完了 (guild={guild_id})")


async def count(guild_id: int) -> int:
    def _count():
        conn = _get_conn()
        n = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE guild_id=? AND role='user'", (guild_id,)
        ).fetchone()[0]
        conn.close()
        return n
    return await asyncio.to_thread(_count)
