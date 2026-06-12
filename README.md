# 凛（Rin）— Discord AI電子秘書ボット

Gemini API + VOICEVOX で動くツンデレAI秘書。

## 必要なもの

| ツール | バージョン |
|--------|-----------|
| Python | 3.11 以上 |
| FFmpeg | 最新安定版 |
| VOICEVOX Engine | 最新版 |

---

## セットアップ手順

### 1. Discord Bot を作成

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. **Bot** タブ → **Add Bot**
3. **Privileged Gateway Intents** で以下をONにする：
   - `SERVER MEMBERS INTENT`
   - `VOICE STATES`
4. **TOKEN** をコピー → `.env` に貼る
5. **OAuth2 > URL Generator** でスコープ `bot`, `applications.commands`、権限 `Send Messages`, `Connect`, `Speak`, `Use Slash Commands` を選んでサーバーに招待

### 2. Gemini API キーを取得

1. [Google AI Studio](https://aistudio.google.com/apikey) → **Create API Key**
2. `.env` に貼る

### 3. VOICEVOX Engine を起動

1. [VOICEVOX 公式](https://voicevox.hiroshiba.jp/) からダウンロード・インストール
2. VOICEVOX を起動（常駐させておく）
3. デフォルトで `http://localhost:50021` で動く

### 4. FFmpeg のインストール

```powershell
winget install --id Gyan.FFmpeg
# または: choco install ffmpeg
```

インストール後 `ffmpeg -version` でパスが通っているか確認。

### 5. パッケージインストール

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### 6. .env を作成

```
DISCORD_TOKEN=your_discord_token
GEMINI_API_KEY=your_gemini_api_key
VOICEVOX_URL=http://localhost:50021
VOICEVOX_SPEAKER_ID=2
GEMINI_MODEL=gemini-2.0-flash
MAX_HISTORY=20
```

---

## Google Workspace 連携（オプション）

Gmail・カレンダー・Driveの情報を `/q` で参照できるようになる。

### 1. Google Cloud Console で設定

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **APIとサービス → ライブラリ** で以下を有効化:
   - Gmail API
   - Google Calendar API
   - Google Drive API
3. **APIとサービス → 認証情報** → **OAuth 2.0 クライアントID** を作成
   - アプリの種類: **デスクトップアプリ**
4. `credentials.json` をダウンロードしてプロジェクトフォルダに置く

### 2. 初回認証（ローカルPCで1回だけ実行）

```powershell
# venv有効化した状態で
python auth_setup.py
```

ブラウザが開くので Google アカウントでログイン → 許可。  
`token.json` が生成される。**これをサーバーの Bot フォルダにコピーする。**

> ⚠️ サーバー上で直接 auth_setup.py を実行してもブラウザが開けないので動かない。
> 必ずローカルPCで実行すること。

### 3. 使い方

`/q` で以下のキーワードを含む質問をすると自動で Workspace を参照する:

| キーワード例 | 参照先 |
|------------|--------|
| メール / mail / gmail / 受信 | Gmail 未読3件 |
| 予定 / カレンダー / スケジュール | Calendar 直近5件 |
| ドライブ / drive / ファイル | Drive 検索 |

---

## コマンド一覧

| コマンド | 説明 |
|---------|------|
| `/q <質問>` | 凛に質問。VCにいれば音声でも返答 |
| `/qclear` | 会話履歴をリセット |
| `/qstatus` | 現在の会話ターン数を確認 |
| `/vjoin` | 凛をVCに呼ぶ（読み上げモード） |
| `/vleave` | 凛をVCから退出させる |

---

## トラブルシューティング

**スラッシュコマンドが表示されない**  
→ 起動後、数分待ってから Discord を再起動。

**VOICEVOXで音が出ない**  
→ VOICEVOX Engine が起動しているか確認。  
→ FFmpeg がPATHに通っているか確認（`ffmpeg -version`）。

**Workspace連携で「token.jsonが見つかりません」と出る**  
→ `auth_setup.py` をローカルで実行して `token.json` を生成・コピーする。

**Workspace連携で「リフレッシュトークンがない」と出る**  
→ `auth_setup.py` を再実行して `token.json` を作り直す。
