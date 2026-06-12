"""
【初回のみ・ローカルPCで実行】Google Workspace 認証セットアップ

使い方:
  1. Google Cloud Console で OAuth2 クライアントID を作成し credentials.json をダウンロード
  2. このスクリプトと同じフォルダに credentials.json を置く
  3. python auth_setup.py を実行 → ブラウザが開くので Google アカウントでログイン
  4. 生成された token.json をサーバーの Bot フォルダにコピーする

以降は Bot が自動でトークンをリフレッシュするため、再実行不要。
（ただし long_lived_refresh_token が失効した場合は再実行が必要）
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(port=0)

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("✅ token.json を生成した。これをサーバーの Bot フォルダにコピーしてください。")
