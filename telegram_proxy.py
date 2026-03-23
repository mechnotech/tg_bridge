"""
Telegram API Proxy — FastAPI
Пересылает запросы на api.telegram.org, жёстко ограничивая:
  - только разрешённый токен бота
  - только разрешённые chat_id
  - не более MAX_RPS запросов в секунду (sliding window)

Запуск:
    pip install fastapi uvicorn httpx
    uvicorn telegram_proxy:app --host 0.0.0.0 --port 80

Env-переменные (или поменяйте константы ниже):
    ALLOWED_BOT_TOKEN   — полный токен вида 123456789:AABBccDDee...
    ALLOWED_CHAT_IDS    — через запятую, напр. "-1001234567890,987654321"
    MAX_RPS             — макс. запросов в секунду (по умолчанию 10)
"""

import os
import time
import collections
import asyncio
import re
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse

ALLOWED_BOT_TOKEN: str = os.getenv("ALLOWED_BOT_TOKEN", "123456789:AABBccDDeeFFggHHiiJJkk")
ALLOWED_CHAT_IDS: set[str] = set(
    filter(None, os.getenv("ALLOWED_CHAT_IDS", "-1001234567890").split(","))
)
MAX_RPS: int = int(os.getenv("MAX_RPS", "10"))
TELEGRAM_BASE = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{8,12}:[A-Za-z0-9_-]{35,}$")


class SlidingWindowRateLimiter:
    """Ограничитель: не более `max_rps` запросов за последнюю 1 секунду."""

    def __init__(self, max_rps: int):
        self.max_rps = max_rps
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            # Удаляем записи старше 1 секунды
            while self._timestamps and now - self._timestamps[0] >= 1.0:
                self._timestamps.popleft()

            if len(self._timestamps) >= self.max_rps:
                return False  # лимит исчерпан

            self._timestamps.append(now)
            return True

limiter = SlidingWindowRateLimiter(MAX_RPS)

app = FastAPI(title="Telegram API Proxy", docs_url=None, redoc_url=None)

# Переиспользуемый async HTTP-клиент
@app.on_event("startup")
async def startup():
    app.state.http = httpx.AsyncClient(timeout=30.0)

@app.on_event("shutdown")
async def shutdown():
    await app.state.http.aclose()


@app.api_route("/bot{token}/{method}", methods=["GET", "POST"])
async def proxy(token: str, method: str, request: Request):
    """
    Принимает:  GET/POST /bot<TOKEN>/<method>?param=value...
    Пересылает: https://api.telegram.org/bot<TOKEN>/<method>?param=value...
    """

    # 1. Проверяем формат токена
    if not TOKEN_RE.match(token):
        raise HTTPException(status_code=403, detail="Invalid bot token format")

    # 2. Проверяем, что токен именно наш
    if token != ALLOWED_BOT_TOKEN:
        raise HTTPException(status_code=403, detail="Bot token not allowed")

    # 3. Проверяем chat_id (ищем в query-параметрах и в JSON-теле)
    params = dict(request.query_params)
    body_bytes = await request.body()
    body_json: Optional[dict] = None

    # Парсим JSON-тело если есть
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type and body_bytes:
        try:
            import json
            body_json = json.loads(body_bytes)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

    chat_id = params.get("chat_id") or (body_json or {}).get("chat_id")

    # chat_id обязателен для методов отправки сообщений
    SEND_METHODS = {
        "sendMessage", "sendPhoto", "sendDocument", "sendVideo",
        "sendAudio", "sendAnimation", "sendVoice", "sendSticker",
        "sendLocation", "sendContact", "sendPoll", "copyMessage",
        "forwardMessage", "sendMediaGroup",
    }
    if method in SEND_METHODS:
        if chat_id is None:
            raise HTTPException(status_code=400, detail="chat_id is required")
        if str(chat_id) not in ALLOWED_CHAT_IDS:
            raise HTTPException(status_code=403, detail="chat_id not allowed")

    # 4. Rate limiting
    allowed = await limiter.acquire()
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"ok": False, "error_code": 429, "description": "Rate limit exceeded. Try again later."},
            headers={"Retry-After": "1"},
        )

    # 5. Пересылаем запрос в Telegram
    tg_url = f"{TELEGRAM_BASE}/bot{token}/{method}"

    # Копируем заголовки, убирая hop-by-hop
    skip_headers = {"host", "content-length", "transfer-encoding", "connection"}
    forward_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in skip_headers
    }

    try:
        tg_response = await app.state.http.request(
            method=request.method,
            url=tg_url,
            params=params if request.method == "GET" else None,
            content=body_bytes if request.method == "POST" else None,
            headers=forward_headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream error: {exc}")

    # 6. Возвращаем ответ Telegram как есть
    excluded_response_headers = {"content-encoding", "transfer-encoding", "connection"}
    response_headers = {
        k: v for k, v in tg_response.headers.items()
        if k.lower() not in excluded_response_headers
    }

    return Response(
        content=tg_response.content,
        status_code=tg_response.status_code,
        headers=response_headers,
        media_type=tg_response.headers.get("content-type", "application/json"),
    )


@app.get("/healtzz")
async def health():
    return {"status": "ok", "rps_limit": MAX_RPS, "allowed_chats": list(ALLOWED_CHAT_IDS)}

