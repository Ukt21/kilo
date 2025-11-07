# Calories Stars — готовый комплект (Render)

Содержимое:
- `backend/fastapi_app.py` — API + загрузка фото + создание инвойса Stars
- `backend/requirements.txt` — зависимости backend
- `backend/render.yaml` — деплой backend+frontend на Render
- `backend/main_ai_bot.py` — бот с обработкой Stars
- `calories-webapp/` — фронтенд (Vite + React). Укажи `VITE_API_BASE` на URL backend

## Быстрый старт
1) Задеплой `calories-backend` (Python Web Service) на Render.
   Env: `CAL_DB_PATH=/var/data/calories_bot.db`, `USER_TZ_OFFSET_HOURS=5`, `OPENAI_API_KEY`, `BOT_TOKEN`, `REQUIRE_AUTH=true`, `FRONTEND_ORIGIN=*`, `UPLOAD_DIR=/var/data/uploads`.
   Подключи Disk на `/var/data`.
2) Задеплой Static Site `calories-webapp`. Укажи `VITE_API_BASE` = URL backend.
3) Поменяй у backend `FRONTEND_ORIGIN` на точный URL фронта и перезапусти.
4) В боте установи `BOT_TOKEN`, запусти `backend/main_ai_bot.py`.
5) /start в боте — выдаёт кнопку WebApp и триал на 7 дней.
6) В WebApp кнопка «Оформить 599⭐» открывает оплату Stars.
