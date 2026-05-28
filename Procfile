# Запуск бота (long polling) + API сервера одновременно через один процесс
# Railway видит этот файл и запускает нужный процесс

# Для бота:
bot: cd backend && python main.py

# Для API (веб-сайт):
web: cd backend && uvicorn api.app:app --host 0.0.0.0 --port $PORT
