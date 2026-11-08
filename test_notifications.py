from app import send_telegram_notification, send_email_notification

print("Проверка Telegram...")
telegram_ok = send_telegram_notification(
    "Тестовое уведомление StarFurshet\nTelegram подключён."
)
print("Telegram:", "Успешно" if telegram_ok else "Ошибка")

print("\nПроверка Gmail...")
email_ok = send_email_notification(
    "Тест StarFurshet",
    "Это тестовое письмо. Проверка отправки уведомлений работает."
)
print("Gmail:", "Успешно" if email_ok else "Ошибка")