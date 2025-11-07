import os, json, hmac, hashlib, base64
from urllib.parse import parse_qsl
from typing import Optional
import aiosqlite
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
from fastapi import FastAPI, HTTPException, Body, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

DB_PATH = os.getenv("CAL_DB_PATH", "/var/data/calories_bot.db")
USER_TZ_OFFSET = int(os.getenv("USER_TZ_OFFSET_HOURS", "5"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "false").lower() == "true"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/var/data/uploads")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

app = FastAPI(title="Calories WebApp API")
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN!="*" else ["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            daily_goal INTEGER DEFAULT 2000,
            plan TEXT DEFAULT 'trial',
            trial_until TEXT,
            renews_at TEXT,
            stars_payer_id TEXT,
            payments_provider TEXT
        );""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            ts TEXT,
            calories INTEGER,
            description TEXT,
            item_name TEXT,
            grams INTEGER,
            source TEXT DEFAULT 'manual',
            photo_url TEXT,
            raw_json TEXT,
            local_ts TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        );""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            created_at TEXT,
            provider TEXT,
            amount_cents INTEGER,
            currency TEXT,
            period_months INTEGER,
            status TEXT,
            provider_payload TEXT
        );""")
        await db.commit()

@app.on_event("startup")
async def on_start():
    await init_db()

# static for uploads
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# --- time helpers ---
def now_utc(): return datetime.now(timezone.utc)
def to_user_tz(dt_utc: datetime): return dt_utc + timedelta(hours=USER_TZ_OFFSET)
def day_bounds_utc_for_user(date_obj):
    local_start = datetime(date_obj.year, date_obj.month, date_obj.day)
    utc_start = (local_start - timedelta(hours=USER_TZ_OFFSET)).replace(tzinfo=timezone.utc)
    utc_end = (utc_start + timedelta(days=1)).replace(tzinfo=timezone.utc)
    return utc_start, utc_end
def month_bounds_utc_for_user(year:int, month:int):
    local_start = datetime(year, month, 1)
    next_month = datetime(year+1,1,1) if month==12 else datetime(year, month+1, 1)
    utc_start = (local_start - timedelta(hours=USER_TZ_OFFSET)).replace(tzinfo=timezone.utc)
    utc_end = (next_month - timedelta(hours=USER_TZ_OFFSET)).replace(tzinfo=timezone.utc)
    days = (next_month - local_start).days
    return utc_start, utc_end, days

# --- db helpers ---
async def db_get_user(tg_id:int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT telegram_id, daily_goal, plan, trial_until, renews_at FROM users WHERE telegram_id=?", (tg_id,))
        row = await cur.fetchone()
        if not row:
            return {}
        return {"telegram_id": row[0], "daily_goal": row[1], "plan": row[2], "trial_until": row[3], "renews_at": row[4]}

async def db_insert_meal(**kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO meals (telegram_id, ts, calories, description, item_name, grams, source, photo_url, raw_json, local_ts) "
            "VALUES (:telegram_id, :ts, :calories, :description, :item_name, :grams, :source, :photo_url, :raw_json, :local_ts)",
            kwargs
        )
        await db.commit()

# --- access control ---
def check_access(user: dict) -> bool:
    from dateutil.parser import isoparse
    now = now_utc()
    plan = user.get("plan")
    if plan == "pro":
        ra = user.get("renews_at")
        if not ra:
            return True
        try:
            return now < isoparse(ra)
        except Exception:
            return True
    if plan == "trial":
        tu = user.get("trial_until")
        try:
            return tu and now < isoparse(tu)
        except Exception:
            return True
    return False

# --- initData validation ---
def _check_init_data(init_data: str) -> Optional[int]:
    if not init_data or not BOT_TOKEN:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        hash_value = data.pop('hash', None)
        items = sorted([f"{k}={v}" for k,v in data.items()])
        data_check_string = "\n".join(items)
        secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
        h = hmac.new(secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()
        if h != hash_value:
            return None
        user_json = data.get('user')
        if not user_json:
            return None
        user = json.loads(user_json)
        return int(user.get('id'))
    except Exception:
        return None

async def _resolve_tg_id(init_data_header: Optional[str]) -> int:
    if REQUIRE_AUTH and not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN required when REQUIRE_AUTH=true")
    if init_data_header:
        tid = _check_init_data(init_data_header)
        if tid:
            async with aiosqlite.connect(DB_PATH) as db:
                now = now_utc()
                trial_until = (now + timedelta(days=7)).isoformat()
                await db.execute(
                    "INSERT INTO users (telegram_id, daily_goal, plan, trial_until) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(telegram_id) DO NOTHING",
                    (tid, 2000, "trial", trial_until)
                )
                await db.commit()
            return tid
        if REQUIRE_AUTH:
            raise HTTPException(status_code=401, detail="Invalid initData")
    return 0

# --- OpenAI helpers ---
ESTIMATE_SYSTEM_PROMPT = ("Ты нутрициолог. Разбивай свободный текст на блюда с порциями и калориями. "
                          "Возвращай JSON:{\"items\":[{\"name\":\"str\",\"grams\":int,\"kcal\":int}],\"total_kcal\":int}.")
COACH_SYSTEM_PROMPT = ("Ты дружелюбный фитнес-коуч. Дай 3–5 конкретных советов и 2 замены, учитывая цель и недавний рацион.")

async def ai_estimate_text(text: str) -> dict:
    if not OPENAI_API_KEY:
        return {"items":[{"name": text[:200], "grams": 0, "kcal": 0}], "total_kcal": 0}
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_ESTIMATE","gpt-4.1-mini"),
        messages=[{"role":"system","content":ESTIMATE_SYSTEM_PROMPT},{"role":"user","content":text}],
        temperature=0.1
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        for it in data.get("items",[]):
            it["name"] = str(it.get("name",""))[:200]
            it["grams"] = int(it.get("grams",0))
            it["kcal"] = int(it.get("kcal",0))
        data["total_kcal"] = int(data.get("total_kcal", sum(i["kcal"] for i in data.get("items",[]))))
        return data
    except Exception:
        return {"items":[{"name": text[:200], "grams": 0, "kcal": 0}], "total_kcal": 0}

async def ai_vision_parse(image_b64: str, prompt: str) -> dict:
    if not OPENAI_API_KEY:
        return {"items":[{"name":"Блюдо","grams":0,"kcal":0}]}
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    res = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_VISION","gpt-4.1-mini"),
        messages=[
            {"role":"system","content":"Всегда возвращай только валидный JSON."},
            {"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"input_image","image_url":{"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        temperature=0.1
    )
    raw = res.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"items":[{"name":"Блюдо","grams":0,"kcal":0}]}

# --- models ---
class AddMealReq(BaseModel):
    calories: int
    description: Optional[str] = ""

class AiAddReq(BaseModel):
    text: str

# --- endpoints ---
@app.get("/api/profile")
async def profile(x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT daily_goal FROM users WHERE telegram_id = ?", (tg_id,))
        row = await cur.fetchone()
        goal = int(row[0]) if row else 2000
    return {"goal": goal, "tzOffset": USER_TZ_OFFSET}

@app.get("/api/summary")
async def summary(period: str = "day", x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT daily_goal FROM users WHERE telegram_id = ?", (tg_id,))
        row = await cur.fetchone()
        goal = int(row[0]) if row else 2000
        if period == "day":
            today = to_user_tz(now_utc()).date()
            s,e = day_bounds_utc_for_user(today)
            cur = await db.execute("SELECT id, ts, calories, item_name FROM meals WHERE telegram_id = ? AND ts >= ? AND ts < ? ORDER BY ts ASC",
                                   (tg_id, s.isoformat(), e.isoformat()))
            rows = await cur.fetchall()
            total = 0; items=[]
            for rid, ts, kc, name in rows:
                total += int(kc)
                items.append({"id": rid, "time": to_user_tz(dateparser.isoparse(ts)).strftime("%H:%M"), "kcal": int(kc), "item": name or ""})
            remaining = max(0, goal - total)
            return {"dateISO": today.isoformat(), "total": total, "goal": goal, "remaining": remaining, "items": items}
        elif period == "month":
            now_local = to_user_tz(now_utc())
            s,e,days = month_bounds_utc_for_user(now_local.year, now_local.month)
            cur = await db.execute("SELECT calories FROM meals WHERE telegram_id = ? AND ts >= ? AND ts < ?",
                                   (tg_id, s.isoformat(), e.isoformat()))
            rows = await cur.fetchall()
            total = sum(int(r[0]) for r in rows)
            avg = total / days if days>0 else 0
            return {"ym": f"{now_local.year}-{str(now_local.month).zfill(2)}", "total": total, "avgPerDay": avg}
        else:
            raise HTTPException(status_code=400, detail="period must be 'day' or 'month'")

@app.post("/api/addmeal")
async def addmeal(req: AddMealReq, x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    ts = now_utc().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (tg_id,))
        await db.execute("UPDATE users SET daily_goal = COALESCE(daily_goal, 2000) WHERE telegram_id = ?", (tg_id,))
        await db.execute("INSERT INTO meals (telegram_id, ts, calories, description, item_name, grams) VALUES (?, ?, ?, ?, ?, ?)",
                         (tg_id, ts, int(req.calories), req.description or "", req.description or "", 0))
        await db.commit()
    return {"ok": True}

@app.post("/api/aiadd")
async def aiadd(req: AiAddReq, x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    data = await ai_estimate_text(req.text or "")
    ts = now_utc().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (telegram_id) VALUES (?)", (tg_id,))
        for it in data.get("items", []):
            await db.execute(
                "INSERT INTO meals (telegram_id, ts, calories, description, item_name, grams, source, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tg_id, ts, int(it.get("kcal",0)), (req.text or "")[:240], it.get("name",""), int(it.get("grams",0)),
                 "vision", json.dumps(data, ensure_ascii=False))
            )
        await db.commit()
    return data

@app.delete("/api/meal/{meal_id}")
async def delete_meal(meal_id: int, x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM meals WHERE id = ? AND telegram_id = ?", (meal_id, tg_id))
        await db.commit()
    return {"ok": True}

# Upload photo
def save_file_local(content: bytes, filename: str) -> str:
    fname = f"{datetime.now().timestamp()}_{filename or 'img.jpg'}"
    path = os.path.join(UPLOAD_DIR, fname)
    with open(path, "wb") as f:
        f.write(content)
    return f"/uploads/{fname}"

@app.post("/api/upload")
async def upload_image(
    type: str = Form(...),
    file: UploadFile = File(...),
    x_telegram_init_data: Optional[str] = Header(None)
):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    user = await db_get_user(tg_id)
    if not check_access(user):
        raise HTTPException(status_code=402, detail="Subscription required")

    content = await file.read()
    if len(content) > 7_000_000:
        raise HTTPException(status_code=413, detail="Image too large")
    photo_url = save_file_local(content, file.filename)

    b64 = base64.b64encode(content).decode("utf-8")
    prompt = ("Это фото кассового чека. Извлеки дату, время и позиции. Верни JSON:{\"date\":\"YYYY-MM-DD\",\"time\":\"HH:mm\",\"items\":[{\"name\":\"str\",\"grams\":int?,\"kcal\":int?}]}"
              if type == "receipt" else
              "Это фото блюда. Определи 1-3 блюда/компонента, оцени граммы и калории. Верни JSON:{\"items\":[{\"name\":\"str\",\"grams\":int,\"kcal\":int}]}")
    data = await ai_vision_parse(b64, prompt)

    used_time = "now"
    base_ts = now_utc()
    if type == "receipt" and data.get("date") and data.get("time"):
        try:
            dt_local = dateparser.isoparse(f"{data['date']} {data['time']}")
            base_ts = (dt_local - timedelta(hours=USER_TZ_OFFSET)).replace(tzinfo=timezone.utc)
            used_time = "receipt"
        except Exception:
            pass

    items_out = []
    for it in data.get("items", []):
        name = str(it.get("name","Блюдо"))[:200]
        grams = int(it.get("grams") or 0)
        kcal = int(it.get("kcal") or 0)
        await db_insert_meal(
            telegram_id=tg_id, ts=base_ts.isoformat(), calories=kcal, description="from photo",
            item_name=name, grams=grams, source=("ocr" if type=="receipt" else "vision"),
            photo_url=photo_url, raw_json=json.dumps(data, ensure_ascii=False),
            local_ts=(f"{data.get('date')} {data.get('time')}" if used_time=="receipt" else None)
        )
        items_out.append({"name": name, "grams": grams, "kcal": kcal, "ts": base_ts.isoformat()})
    return {"ok": True, "inferred_type": type, "used_time": used_time, "items": items_out}

# Subscription: Stars
@app.get("/api/subscribe/status")
async def subscribe_status(x_telegram_init_data: Optional[str] = Header(None)):
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    user = await db_get_user(tg_id)
    now = now_utc()
    trial_days_left = None
    if user and user.get("trial_until"):
        try:
            trial_days_left = max(0, (dateparser.isoparse(user["trial_until"]) - now).days)
        except Exception:
            trial_days_left = 0
    return {
        "plan": user.get("plan","trial"),
        "trial_until": user.get("trial_until"),
        "renews_at": user.get("renews_at"),
        "trial_days_left": trial_days_left
    }

@app.post("/api/subscribe/create")
async def subscribe_create(x_telegram_init_data: Optional[str] = Header(None)):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    tg_id = await _resolve_tg_id(x_telegram_init_data)
    if not tg_id:
        raise HTTPException(status_code=401, detail="Auth required")

    payload = f"sub_monthly:{tg_id}:{int(now_utc().timestamp())}"
    prices = [{"label": "Monthly PRO", "amount": 599}]  # 599 Stars

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{TELEGRAM_API}/createInvoiceLink", json={
            "title":"Calories PRO — 1 месяц",
            "description":"ИИ-распознавание фото, отчёты и мониторинг. Подписка на 1 месяц.",
            "payload": payload,
            "currency":"XTR",
            "prices": prices
        })
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(status_code=500, detail=f"Telegram error: {data}")
        return {"invoice_url": data["result"]}
