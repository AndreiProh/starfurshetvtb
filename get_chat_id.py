import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")

if not token:
    print("Ошибка: TELEGRAM_BOT_TOKEN не найден в .env")
    raise SystemExit(1)

url = f"https://api.telegram.org/bot{token}/getUpdates"

try:
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not data.get("ok"):
        print("Telegram вернул ошибку.")
    elif not data.get("result"):
        print("Сообщений нет. Отправь боту /start и повтори.")
    else:
        found = set()

        for update in data["result"]:
            message = update.get("message") or update.get("channel_post")

            if message:
                chat = message["chat"]
                chat_id = chat["id"]

                if chat_id not in found:
                    found.add(chat_id)
                    print("Chat ID:", chat_id)
                    print("Тип чата:", chat.get("type"))
                    print("---")

except Exception as error:
    print("Не удалось получить данные:", type(error).__name__)