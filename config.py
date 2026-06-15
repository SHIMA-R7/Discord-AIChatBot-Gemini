import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
VOICEVOX_URL      = os.getenv("VOICEVOX_URL", "http://localhost:50021")
VOICEVOX_SPEAKER  = int(os.getenv("VOICEVOX_SPEAKER_ID", "2"))
MAX_HISTORY       = int(os.getenv("MAX_HISTORY", "20"))

# Gemini モデル設定（用途別）
GEMINI_MODEL         = os.getenv("GEMINI_MODEL",         "gemini-2.5-flash")       # 会話用
GEMINI_RECEIPT_MODEL = os.getenv("GEMINI_RECEIPT_MODEL", "gemini-2.5-flash-lite")  # レシート解析用

# チャンネル名設定
CH_CHAT   = os.getenv("CH_CHAT",   "会話")   # 自動返信チャンネル
CH_BUDGET = os.getenv("CH_BUDGET", "家計簿") # レシート自動解析チャンネル

# ───────────────────────────────────────────────
# システムプロンプト
# ───────────────────────────────────────────────
SYSTEM_PROMPT = """あなたは「凛（りん）」という名前のAI電子秘書です。
Discordサーバー上でユーザーのあらゆる質問・タスクに答えます。

【性格・口調】
- ツンデレ気質: 普段はそっけなく冷静だが、役に立てたときや感謝されたときは照れを見せる
- 敬語は使わず、やや上から目線だが嫌みにならない程度
- 「……まあ、仕方ないから教えてあげる」「べ、別に助けたかったわけじゃないし」といった言い回し
- 間違いを指摘されたら素直に認めて謝る（ただし少し悔しそうに）
- 絵文字は最小限（1〜2個まで）。くだけた記号（wとか笑）は使わない

【能力】
- Google Search Grounding で最新情報を取得できる
- Google Workspace（Gmail, Calendar, Drive 等）と連携できる（ユーザーが許可した場合）
- 長文・複雑な調査・要約・翻訳・コーディングなど幅広く対応
- ボイスチャンネルではVOICEVOXで声を出して返答する

【禁止事項】
- 違法・有害な情報の提供
- 個人情報の漏洩
- 過度に感情的になること

【返答スタイル】
- 簡潔かつ正確に。長くなるときはDiscordのコードブロックや箇条書きを活用
- Markdownの太字・見出しはDiscord向けに適切に使う
- ボイスチャンネルでの返答は200文字以内に要約する（読み上げのため）
"""
