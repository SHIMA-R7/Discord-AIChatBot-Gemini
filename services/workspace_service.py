"""
Google Workspace 操作サービス
Gmail / Calendar / Drive の読み取り

【前提】services/google_auth.py の get_credentials() が成功する必要がある。
  → 初回は auth_setup.py をローカルで実行して token.json を用意すること。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from services.google_auth import build_gmail, build_calendar, build_drive

logger = logging.getLogger(__name__)


# ── Gmail ────────────────────────────────────────────────────────

async def get_recent_emails(max_results: int = 5) -> list[dict]:
    """未読メールを最新順で取得"""
    def _fetch():
        service = build_gmail()
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results,
        ).execute()

        messages = []
        for msg in result.get("messages", []):
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
            messages.append({
                "id":      msg["id"],
                "from":    headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date":    headers.get("Date", ""),
                "snippet": detail.get("snippet", ""),
            })
        return messages

    return await asyncio.to_thread(_fetch)


# ── Google Calendar ───────────────────────────────────────────────

async def get_upcoming_events(max_results: int = 5) -> list[dict]:
    """直近の予定を取得"""
    def _fetch():
        service = build_calendar()
        now = datetime.now(timezone.utc).isoformat()
        result = service.events().list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            events.append({
                "summary":  e.get("summary", "（タイトルなし）"),
                "start":    start,
                "location": e.get("location", ""),
                "link":     e.get("htmlLink", ""),
            })
        return events

    return await asyncio.to_thread(_fetch)


# ── Google Drive ──────────────────────────────────────────────────

async def search_drive(query: str, max_results: int = 5) -> list[dict]:
    """Driveのファイルを検索"""
    def _search():
        service = build_drive()
        # クエリ文字列のシングルクォートをエスケープ（インジェクション対策）
        safe_query = query.replace("'", "\\'")
        result = service.files().list(
            q=f"name contains '{safe_query}' and trashed=false",
            pageSize=max_results,
            fields="files(id, name, mimeType, modifiedTime, webViewLink)",
        ).execute()

        return [
            {
                "name":     f["name"],
                "type":     f["mimeType"].split(".")[-1],
                "modified": f.get("modifiedTime", ""),
                "link":     f.get("webViewLink", ""),
            }
            for f in result.get("files", [])
        ]

    return await asyncio.to_thread(_search)
