# bot.py
from plantbot.handlers import build_app

if __name__ == "__main__":
    app = build_app()
    app.run_polling(allowed_updates=["message","edited_message","callback_query"])
