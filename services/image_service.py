"""
画像生成サービス

処理フロー:
  1. Gemini で日本語の要望 → 英語の画像生成プロンプトに変換
  2. Cloudflare Workers AI (Flux-1-schnell) に投げて画像バイト列を取得
     ※ Flux は JSON {"result": {"image": "<base64文字列>"}} を返す
"""
from __future__ import annotations

import base64
import io
import logging

import aiohttp
from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=config.GEMINI_API_KEY)

# ── プロンプト生成 ────────────────────────────────────────────────

_PROMPT_SYSTEM = """\
あなたは画像生成AIへの英語プロンプトを作る専門家です。
ユーザーの日本語のリクエストを、Stable DiffusionやFluxで最高の結果が出る
英語プロンプトに変換してください。

ルール:
- 出力は英語のプロンプト文のみ。説明・前置き・記号は不要。
- 画風・構図・照明・細部描写を自然に補完して豊かにする。
- ネガティブ要素（ぼけ、低品質など）は含めない。
- 100語以内に収める。
"""

async def _generate_prompt(user_request: str) -> str:
    """日本語リクエスト → 英語画像生成プロンプト"""
    try:
        response = await _client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=user_request,
            config=types.GenerateContentConfig(
                system_instruction=_PROMPT_SYSTEM,
                max_output_tokens=200,
            ),
        )
        prompt = (response.text or "").strip()
        logger.info(f"生成プロンプト: {prompt}")
        return prompt
    except Exception as e:
        logger.error(f"プロンプト生成エラー: {e}", exc_info=True)
        raise


# ── Cloudflare Workers AI 呼び出し ────────────────────────────────

async def _call_cloudflare(prompt: str) -> bytes:
    """
    Cloudflare Workers AI に投げて PNG バイト列を返す。
    Flux-1-schnell のレスポンス形式:
      {"result": {"image": "<base64エンコードされたPNG>"}, "success": true, ...}
    """
    if not config.CF_ACCOUNT_ID or not config.CF_API_TOKEN:
        raise RuntimeError(
            "CF_ACCOUNT_ID または CF_API_TOKEN が未設定です。.env を確認してください。"
        )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{config.CF_ACCOUNT_ID}/ai/run/{config.CF_IMAGE_MODEL}"
    )
    headers = {
        "Authorization": f"Bearer {config.CF_API_TOKEN}",
        "Content-Type":  "application/json",
    }
    payload = {"prompt": prompt, "num_steps": 4}  # Flux-schnell は4ステップが最適

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Cloudflare API エラー {resp.status}: {body}")

            content_type = resp.headers.get("Content-Type", "")

            if "application/json" in content_type:
                # Flux: JSON の中に base64 画像が入っている
                data = await resp.json()
                b64 = (
                    data.get("result", {}).get("image")
                    or data.get("image")  # フォールバック
                )
                if not b64:
                    raise RuntimeError(f"画像データがレスポンスに含まれていない: {data}")
                image_bytes = base64.b64decode(b64)
            else:
                # SDXL など: 直接バイナリ
                image_bytes = await resp.read()

            logger.info(f"画像生成完了: {len(image_bytes)//1024}KB")
            return image_bytes


# ── 公開 API ─────────────────────────────────────────────────────

class ImageResult:
    def __init__(
        self,
        image_bytes: bytes | None = None,
        prompt_en:   str = "",
        error:       str = "",
    ):
        self.image_bytes = image_bytes
        self.prompt_en   = prompt_en
        self.error       = error

    @property
    def ok(self) -> bool:
        return self.image_bytes is not None and not self.error


async def generate(user_request: str) -> ImageResult:
    """
    日本語リクエストから画像を生成して ImageResult を返す。
    呼び出し元は result.ok を確認すること。
    """
    try:
        prompt_en   = await _generate_prompt(user_request)
        image_bytes = await _call_cloudflare(prompt_en)
        return ImageResult(image_bytes=image_bytes, prompt_en=prompt_en)
    except Exception as e:
        logger.error(f"画像生成エラー: {e}", exc_info=True)
        return ImageResult(error=str(e))
