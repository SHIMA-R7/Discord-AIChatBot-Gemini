"""
Google Workspace OAuth2 認証管理
初回のみブラウザ認証が必要。以降は token.json を自動更新。
"""
from __future__ import annotations

import asyncio
import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"


def get_credentials() -> Credentials:
    """token.json があれば再利用、なければブラウザ認証を起動"""
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES
            )
            # ポート0 = OSが空きポートを自動選択
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info("token.json を保存した")

    return creds


# ── 各サービスのビルダー ──────────────────────────────────────────

def build_gmail():
    return build("gmail", "v1", credentials=get_credentials())

def build_calendar():
    return build("calendar", "v3", credentials=get_credentials())

def build_drive():
    return build("drive", "v3", credentials=get_credentials())