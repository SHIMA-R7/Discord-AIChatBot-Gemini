"""
Google Workspace 操作サービス
Gemini の Function Calling から呼ばれる関数群
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


async def send_email(to: str, subject: str, body: str) -> str:
    """メールを送信する"""
    import base64
    from email.mime.text import MIMEText

    def _send():
        msg = MIMEText(body)
        msg["to"]      = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service = build_gmail()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return f"{to} へのメール送信完了"

    return await asyncio.to_thread(_send)


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


async def create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
) -> str:
    """カレンダーに予定を追加（ISO8601形式: 2025-04-01T14:00:00+09:00）"""
    def _create():
        service = build_calendar()
        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_datetime, "timeZone": "Asia/Tokyo"},
            "end":   {"dateTime": end_datetime,   "timeZone": "Asia/Tokyo"},
        }
        created = service.events().insert(
            calendarId="primary", body=event
        ).execute()
        return f"予定「{title}」を追加した → {created.get('htmlLink', '')}"

    return await asyncio.to_thread(_create)


# ── Google Drive ──────────────────────────────────────────────────

async def search_drive(query: str, max_results: int = 5) -> list[dict]:
    """Driveのファイルを検索"""
    def _search():
        service = build_drive()
        result = service.files().list(
            q=f"name contains '{query}' and trashed=false",
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