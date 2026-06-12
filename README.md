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
   - `VOICE STATES` （自動でON）
4. **TOKEN** をコピー → `.env` に貼る
5. **OAuth2 > URL Generator** で以下のスコープ・権限を選択してサーバーに招待：
   - Scopes: `bot`, `applications.commands`
   - Permissions: `Send Messages`, `Connect`, `Speak`, `Use Slash Commands`

### 2. Gemini API キーを取得

1. [Google AI Studio](https://aistudio.google.com/apikey) → **Create API Key**
2. `.env` に貼る

### 3. VOICEVOX Engine を起動

1. [VOICEVOX 公式](https://voicevox.hiroshiba.jp/) からダウンロード・インストール
2. VOICEVOXを起動する（常駐させておく）
3. デフォルトで `http://localhost:50021` で動く

### 4. FFmpeg のインストール

```powershell
# winget でインストール
winget install --id Gyan.FFmpeg

# または Chocolatey
choco install ffmpeg
```

インストール後、`ffmpeg -version` でパスが通っているか確認。

### 5. プロジェクトのセットアップ

```powershell
# リポジトリに移動
cd discord-bot

# 仮想環境を作成・有効化
python -m venv venv
.\venv\Scripts\activate

# 依存パッケージをインストール
pip install -r requirements.txt

# .env ファイルを作成
copy .env.example .env
# .env をエディタで開いてAPIキー等を設定
```

### 6. 話者IDを確認（任意）

```powershell
python voicevox_speakers.py
```

出力された ID を `.env` の `VOICEVOX_SPEAKER_ID` に設定する。

### 7. 起動

```powershell
python bot.py
```

---

## コマンド一覧

| コマンド | 説明 |
|---------|------|
| `/q <質問>` | どのチャンネルからでも凛に質問。VCにいれば音声でも返答 |
| `/qclear` | 会話履歴をリセット |
| `/qstatus` | 現在の会話ターン数を確認 |
| `/vjoin` | 凛をVCに呼ぶ |
| `/vleave` | 凛をVCから退出させる |

---

## 仕組み

```
ユーザー /q 質問
    ↓
Bot Core (discord.py)
    ↓
Gemini API (gemini-2.0-flash)
    ├── Google Search Grounding（最新情報を検索）
    └── 会話履歴（サーバーごとに保持）
    ↓
テキスト応答 → Discordに送信
    ↓ (VCにいる場合)
VOICEVOX → WAV生成
    ↓
FFmpeg → VCで再生
```

---

## トラブルシューティング

**スラッシュコマンドが表示されない**
→ `bot.py` 起動後、数分待ってから Discord を再起動してください。
　 初回同期に時間がかかる場合があります。

**VOICEVOXで音が出ない**
→ VOICEVOX Engine が起動しているか確認（タスクトレイ）。
→ FFmpeg がPATHに通っているか確認。
→ Discord の「マイク」ではなく「スピーカー」設定を確認。

**Gemini がエラーを返す**
→ APIキーが正しいか確認。
→ Google AI Studio でGemini APIが有効になっているか確認。
