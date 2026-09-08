import os
from google import genai

api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

print("📋 當前 API Key 可存取的模型清單：\n" + "-" * 40)
for model in client.models.list():
    # 只篩選支援文字生成的模型，過濾掉語音或純嵌入模型
    if "generateContent" in model.supported_actions:
        # 去除 models/ 前綴，印出乾淨的 Model ID
        model_id = model.name.replace("models/", "")
        print(f"🔹 {model_id} ({model.display_name})")
