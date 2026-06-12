"""
Google Workspace OAuth2 認証管理

【重要】初回認証手順:
  サーバー環境ではブラウザが開けないため、run_local_server() は使えない。
  初回だけローカルPCで auth_setup.py を実行して token.json を生成し、
  サーバーにコピーすること（下記 README 参照）。
  以降は token.json の自動リフレッシュで動作する。
"""
from __future__ import annotations

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",   # 読み取りのみに絞る（安全のため）
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"


def get_credentials() -> Credentials:
    """
    token.json があればリフレッシュして返す。
    token.json がない場合は RuntimeError を投げる（サーバー環境では自動認証不可）。
    初回は別途 auth_setup.py をローカルで実行すること。
    """
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(
            "token.json が見つかりません。"
            "ローカルPCで auth_setup.py を実行して token.json を生成・コピーしてください。"
        )

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
            logger.info("token.json をリフレッシュした")
        else:
            raise RuntimeError(
                "token.json が無効です（リフレッシュトークンがない）。"
                "auth_setup.py で再認証してください。"
            )

    return creds


def build_gmail():
    return build("gmail", "v1", credentials=get_credentials())

def build_calendar():
    return build("calendar", "v3", credentials=get_credentials())

def build_drive():
    return build("drive", "v3", credentials=get_credentials())
