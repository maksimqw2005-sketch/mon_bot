# -*- coding: utf-8 -*-
"""
МОНОКОР — бот-выдача подарков с проверкой подписки на канал.
Работает без сторонних библиотек: нужен только Python 3.9+.
Настройки — в config.json, подарки — в gifts.json.
"""
import json, os, re, sqlite3, sys, time, html, urllib.request, urllib.parse, urllib.error

DIR = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(DIR, "config.json"), encoding="utf-8"))
TOKEN = (os.environ.get("BOT_TOKEN") or CFG["token"]).strip()
CHANNEL = CFG["channel"].strip()                 # например "@monokorlab"
CHANNEL_URL = CFG.get("channel_url") or f"https://t.me/{CHANNEL.lstrip('@')}"
ADMIN_ID = int(os.environ.get("ADMIN_ID") or CFG.get("admin_id") or 0)          # твой числовой Telegram ID
API = f"https://api.telegram.org/bot{TOKEN}/"

def load_gifts():
    g = json.load(open(os.path.join(DIR, "gifts.json"), encoding="utf-8"))
    index = {}
    for key, v in g.items():
        for name in [key] + v.get("aliases", []):
            index[norm(name)] = key
    return g, index

def norm(s):
    s = (s or "").upper().replace("Ё", "Е")
    return re.sub(r"[^A-ZА-Я0-9]", "", s)

# ---------- Telegram API ----------
def api(method, **params):
    data = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    req = urllib.request.Request(API + method, data=urllib.parse.urlencode(data).encode())
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.loads(r.read().decode()).get("result")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        print(f"[api] {method} {e.code}: {body[:300]}", file=sys.stderr)
        return None

def send(chat_id, text, kb=None, preview=False):
    return api("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
               disable_web_page_preview=not preview, reply_markup=kb)

def send_document(chat_id, path, caption=None):
    import uuid, mimetypes
    full = os.path.join(DIR, path)
    if not os.path.exists(full):
        print(f"[doc] нет файла {full}", file=sys.stderr); return None
    b = uuid.uuid4().hex; parts = []
    def field(n, v): parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n".encode())
    field("chat_id", chat_id)
    if caption: field("caption", caption); field("parse_mode", "HTML")
    fn = os.path.basename(full)
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{fn}\"\r\nContent-Type: application/pdf\r\n\r\n".encode())
    parts.append(open(full, "rb").read()); parts.append(f"\r\n--{b}--\r\n".encode())
    req = urllib.request.Request(API + "sendDocument", data=b"".join(parts), headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return json.loads(r.read().decode()).get("result")
    except urllib.error.HTTPError as e:
        print("[doc]", e.code, e.read().decode(errors="ignore")[:300], file=sys.stderr); return None

def is_subscribed(user_id):
    r = api("getChatMember", chat_id=CHANNEL, user_id=user_id)
    if not r:
        return False
    st = r.get("status")
    return st in ("member", "administrator", "creator") or (st == "restricted" and r.get("is_member"))

# ---------- База ----------
DATA_DIR = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else DIR)
db = sqlite3.connect(os.path.join(DATA_DIR, "bot.db"), check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, name TEXT, first_seen INTEGER, state TEXT, asked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(user_id INTEGER, gift TEXT, kind TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS leads(user_id INTEGER, username TEXT, name TEXT, gift TEXT, niche TEXT, ts INTEGER);
""")

def touch_user(u):
    name = " ".join(x for x in [u.get("first_name"), u.get("last_name")] if x)
    db.execute("INSERT OR IGNORE INTO users(id,username,name,first_seen) VALUES(?,?,?,?)",
               (u["id"], u.get("username"), name, int(time.time())))
    db.execute("UPDATE users SET username=?, name=? WHERE id=?", (u.get("username"), name, u["id"]))
    db.commit()

def user_row(uid):
    return db.execute("SELECT username,name,state,asked FROM users WHERE id=?", (uid,)).fetchone()

def set_state(uid, state):
    db.execute("UPDATE users SET state=? WHERE id=?", (state, uid)); db.commit()

def log(uid, gift, kind):
    db.execute("INSERT INTO events VALUES(?,?,?,?)", (uid, gift, kind, int(time.time()))); db.commit()

def last_gift(uid):
    r = db.execute("SELECT gift FROM events WHERE user_id=? AND kind='delivered' ORDER BY ts DESC LIMIT 1", (uid,)).fetchone()
    return r[0] if r else None

# ---------- Логика ----------
GIFTS, INDEX = load_gifts()

def sub_keyboard(gift_key):
    return {"inline_keyboard": [
        [{"text": "📢 Подписаться на канал", "url": CHANNEL_URL}],
        [{"text": "✅ Я подписался — проверить", "callback_data": f"check:{gift_key}"}]]}

def deliver(uid, gift_key):
    g = GIFTS[gift_key]
    if g.get("file"):
        send_document(uid, g["file"], g["text"])
    else:
        send(uid, g["text"])
    log(uid, gift_key, "delivered")
    row = user_row(uid)
    if row and not row[3]:
        db.execute("UPDATE users SET asked=1 WHERE id=?", (uid,)); db.commit()
        time.sleep(1.5)
        send(uid, CFG.get("ask_text", "Кстати, у тебя есть свой бизнес или проект?"),
             {"inline_keyboard": [[{"text": "Да, есть", "callback_data": "biz:yes"},
                                   {"text": "Пока нет", "callback_data": "biz:no"}]]})

CATS = [("bots", "🤖 Боты для бизнеса"), ("clients", "💼 Клиенты и продажи"), ("content", "✍️ Контент"),
        ("prompts", "🧠 Промты"), ("guides", "📚 Гайды (PDF)")]
MENU_BTN = "📚 Все материалы"
REPLY_KB = {"keyboard": [[{"text": MENU_BTN}]], "resize_keyboard": True, "is_persistent": True}

def menu_kb():
    rows = [[{"text": f"{name} · {sum(1 for g in GIFTS.values() if g.get('cat') == c)}", "callback_data": f"cat:{c}"}] for c, name in CATS
            if any(g.get("cat") == c for g in GIFTS.values())]
    rows.append([{"text": "📢 Канал МОНОКОР", "url": CHANNEL_URL}])
    return {"inline_keyboard": rows}

def cat_kb(c):
    rows = [[{"text": g.get("title", k), "callback_data": f"g:{k}"}] for k, g in GIFTS.items() if g.get("cat") == c]
    rows.append([{"text": "← Все разделы", "callback_data": "menu"}])
    return {"inline_keyboard": rows}

MENU_TEXT = "📚 <b>Все материалы МОНОКОР</b>\n\nВыбери раздел — внутри промты, шаблоны ботов и гайды. Всё бесплатно для подписчиков канала."

def show_menu(uid, edit_msg=None):
    if edit_msg:
        api("editMessageText", chat_id=uid, message_id=edit_msg, text=MENU_TEXT, parse_mode="HTML", reply_markup=menu_kb())
    else:
        send(uid, MENU_TEXT, menu_kb())

def handle_word(uid, word):
    key = INDEX.get(norm(word))
    if not key:
        return False
    log(uid, key, "asked")
    if is_subscribed(uid):
        deliver(uid, key)
    else:
        log(uid, key, "need_sub")
        send(uid, CFG.get("sub_text", "🎁 Подарок уже ждёт!\n\nОн доступен подписчикам канала. Подпишись и нажми «Проверить» — бот сразу пришлёт материал."),
             sub_keyboard(key))
    return True

def on_message(m):
    u = m.get("from") or {}
    if not u or m.get("chat", {}).get("type") != "private":
        return
    uid = u["id"]; touch_user(u)
    text = (m.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/start"):
        payload = text[6:].strip()
        if payload and handle_word(uid, payload):
            return
        send(uid, CFG.get("start_text", "Привет! Напиши кодовое слово из ролика — и я пришлю подарок 🎁"), REPLY_KB)
        show_menu(uid)
        return
    if text in (MENU_BTN, "/menu"):
        show_menu(uid); return
    if text == "/stats" and uid == ADMIN_ID:
        send(uid, stats()); return
    if text == "/leads" and uid == ADMIN_ID:
        send(uid, leads_text()); return
    if text == "/words":
        show_menu(uid); return
    if handle_word(uid, text):
        return
    row = user_row(uid)
    if row and row[2] == "niche":
        set_state(uid, None)
        gift = last_gift(uid)
        db.execute("INSERT INTO leads VALUES(?,?,?,?,?,?)", (uid, row[0], row[1], gift, text[:500], int(time.time()))); db.commit()
        send(uid, CFG.get("lead_thanks", "Спасибо! Посмотрю и напишу тебе лично с идеями, что можно автоматизировать 🤝"))
        if ADMIN_ID:
            who = f"@{row[0]}" if row[0] else f'<a href="tg://user?id={uid}">{html.escape(row[1] or str(uid))}</a>'
            send(ADMIN_ID, f"🔥 <b>Новый лид</b>\n{who}\nПодарок: {html.escape(gift or '—')}\nБизнес: {html.escape(text[:500])}")
        return
    send(uid, CFG.get("unknown_text", "Не нашёл такое слово 🤔 Проверь, как оно написано под роликом — или выбери в меню 👇"), menu_kb())

def on_callback(c):
    u = c["from"]; uid = u["id"]; touch_user(u); data = c.get("data", "")
    if data.startswith("check:"):
        key = data.split(":", 1)[1]
        if key in GIFTS and is_subscribed(uid):
            api("answerCallbackQuery", callback_query_id=c["id"], text="Подписка есть ✅")
            msg = c.get("message")
            if msg:
                api("editMessageText", chat_id=uid, message_id=msg["message_id"], text="Спасибо за подписку! Держи подарок 👇")
            deliver(uid, key)
        else:
            api("answerCallbackQuery", callback_query_id=c["id"], show_alert=True,
                text="Пока не вижу подписку. Подпишись на канал и нажми ещё раз.")
    elif data == "menu":
        api("answerCallbackQuery", callback_query_id=c["id"])
        show_menu(uid, (c.get("message") or {}).get("message_id"))
    elif data.startswith("cat:"):
        api("answerCallbackQuery", callback_query_id=c["id"])
        cat = data[4:]; name = dict(CATS).get(cat, "Раздел")
        msg = (c.get("message") or {}).get("message_id")
        api("editMessageText", chat_id=uid, message_id=msg, text=f"<b>{name}</b>\n\nНажми на материал 👇", parse_mode="HTML", reply_markup=cat_kb(cat))
    elif data.startswith("g:"):
        api("answerCallbackQuery", callback_query_id=c["id"])
        key = data[2:]
        if key in GIFTS: handle_word(uid, key)
    elif data == "biz:yes":
        api("answerCallbackQuery", callback_query_id=c["id"])
        set_state(uid, "niche")
        send(uid, CFG.get("ask_niche", "Класс! Напиши в одном сообщении, чем занимаешься — подскажу, что можно отдать нейросети."))
    elif data == "biz:no":
        api("answerCallbackQuery", callback_query_id=c["id"])
        send(uid, CFG.get("no_biz", "Понял 👌 Новые подарки и разборы — в канале."))

def stats():
    q = lambda s, *a: db.execute(s, a).fetchone()[0]
    lines = [f"👥 Пользователей: {q('SELECT COUNT(*) FROM users')}",
             f"🎁 Выдано подарков: {q('SELECT COUNT(*) FROM events WHERE kind=?', 'delivered')}",
             f"🔒 Просили подписку: {q('SELECT COUNT(DISTINCT user_id) FROM events WHERE kind=?', 'need_sub')}",
             f"🔥 Лидов: {q('SELECT COUNT(*) FROM leads')}", "", "По словам (запросы / выдачи):"]
    for k in GIFTS:
        lines.append(f"• {k}: {q('SELECT COUNT(*) FROM events WHERE gift=? AND kind=?', k, 'asked')} / {q('SELECT COUNT(*) FROM events WHERE gift=? AND kind=?', k, 'delivered')}")
    return "\n".join(lines)

def leads_text():
    rows = db.execute("SELECT username,name,gift,niche,ts FROM leads ORDER BY ts DESC LIMIT 20").fetchall()
    if not rows:
        return "Лидов пока нет."
    return "\n\n".join(f"{'@'+r[0] if r[0] else html.escape(r[1] or '')} · {time.strftime('%d.%m %H:%M', time.localtime(r[4]))}\n{html.escape(r[2] or '—')}: {html.escape(r[3])}" for r in rows)

def main():
    me = api("getMe")
    if not me:
        sys.exit("Не удалось подключиться: проверь token в config.json")
    print(f"Бот @{me['username']} запущен. Канал: {CHANNEL}. Ctrl+C — остановить.")
    api("setMyCommands", commands=[{"command": "start", "description": "Начать"}, {"command": "menu", "description": "Все материалы"}])
    offset = None
    while True:
        try:
            ups = api("getUpdates", timeout=50, offset=offset, allowed_updates=["message", "callback_query"]) or []
            for up in ups:
                offset = up["update_id"] + 1
                try:
                    if "message" in up: on_message(up["message"])
                    elif "callback_query" in up: on_callback(up["callback_query"])
                except Exception as e:
                    print("[handler]", repr(e), file=sys.stderr)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[poll]", repr(e), file=sys.stderr); time.sleep(5)

if __name__ == "__main__":
    main()
