# Zerops Installer Bot 🤖

ربات تلگرام که با گرفتن توکن Zerops + توکن بات + کلید 9router،
بصورت خودکار پروژه جدید می‌سازه و hermes + 9router رو نصب می‌کنه.

## Deploy رایگان (Render)

1. https://render.com → New → Web Service
2. ریپو: `mmdgk123/zerops-installer-bot` (پرایوت — اکانت گیت‌هابت رو وصل کن)
3. Build: `pip install -r requirements.txt` — Start: `python server.py`
4. Env: `BOT_TOKEN` = توکن باتی که از @BotFather ساختی
5. برو تلگرام به باتت `/start` بده و مراحل رو برو

## فلو

/start → توکن Zerops → توکن بات هرمس جدید → کلید 9router (یا skip) → اسم پروژه → ساخت خودکار
