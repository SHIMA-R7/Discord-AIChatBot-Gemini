from google import genai
import config

client = genai.Client(api_key=config.GEMINI_API_KEY)

print("--- 利用可能なモデル一覧 ---")
for model in client.models.list():
    print(f"Model ID: {model.name}")