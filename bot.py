import os, asyncio, logging, sqlite3, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError
from telethon import events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("telegram-board-v2")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
TZ = ZoneInfo(os.getenv("TZ", "Asia/Kolkata"))
DB_PATH = os.getenv("DB_PATH", "board_v2.sqlite3")

tg = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS destinations
 (chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0)""")
db.execute("""CREATE TABLE IF NOT EXISTS events
 (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, chat_id INTEGER,
  chat_title TEXT, status TEXT NOT NULL, detail TEXT, message_id INTEGER)""")
db.execute("""CREATE TABLE IF NOT EXISTS settings
 (key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
db.commit()

def get_setting(k, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return row[0] if row else default

def set_setting(k, v):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    db.commit()

def is_admin(update):
    user = update.effective_user
    return user is not None and user.id == ADMIN_ID

def log_event(chat_id, title, status, detail="", message_id=None):
    db.execute("INSERT INTO events(ts,chat_id,chat_title,status,detail,message_id) VALUES(?,?,?,?,?,?)",
               (time.time(), chat_id, title, status, str(detail)[:900], message_id))
    db.commit()

def enabled_destinations():
    return db.execute("SELECT chat_id,title FROM destinations WHERE enabled=1 ORDER BY title").fetchall()

async def menu(update_or_query, text="📊 Telegram Board V2"):
    kb = [
        [InlineKeyboardButton("📈 Today's Analytics", callback_data="stats"),
         InlineKeyboardButton("🔎 Error Report", callback_data="errors")],
        [InlineKeyboardButton("👥 Load/Manage Groups", callback_data="groups"),
         InlineKeyboardButton("✉️ Set Message", callback_data="setmsg")],
        [InlineKeyboardButton("▶️ Start Schedule", callback_data="start"),
         InlineKeyboardButton("⏸ Stop Schedule", callback_data="stop")],
        [InlineKeyboardButton("🧪 Send Test", callback_data="test")]
    ]
    markup = InlineKeyboardMarkup(kb)
    if hasattr(update_or_query, "edit_message_text"):
        try:
            await update_or_query.edit_message_text(text, reply_markup=markup)
        except Exception:
            await update_or_query.message.reply_text(text, reply_markup=markup)
    else:
        await update_or_query.effective_message.reply_text(text, reply_markup=markup)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await update.message.reply_text("⛔ Admin only.")
    await menu(update)

async def stats_text():
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = db.execute("SELECT status, COUNT(*) FROM events WHERE ts>=? GROUP BY status", (start,)).fetchall()
    counts = {k:v for k,v in rows}
    total = sum(counts.values())
    groups = len(enabled_destinations())
    last = db.execute("SELECT ts,chat_title,status,detail FROM events ORDER BY id DESC LIMIT 1").fetchone()
    lastline = "No attempts recorded today yet."
    if last:
        lt = datetime.fromtimestamp(last[0], TZ).strftime("%H:%M:%S")
        lastline = f"{lt} · {last[1]} · {last[2]} · {last[3][:100]}"
    return (f"📈 TODAY'S ANALYTICS ({now.strftime('%d %b %Y')})\\n\\n"
            f"📨 Total attempts: {total}\\n"
            f"✅ Sent successfully: {counts.get('success',0)}\\n"
            f"❌ Failed: {counts.get('failed',0)}\\n"
            f"⚠️ Delete events observed: {counts.get('deleted',0)}\\n"
            f"👥 Selected groups: {groups}\\n"
            f"⏱ Schedule: {'RUNNING' if get_setting('running','0')=='1' else 'STOPPED'}\\n"
            f"🔁 Interval: {get_setting('interval','10')} minutes\\n\\n"
            f"🕘 Latest activity: {lastline}\\n\\n"
            "Note: Telegram does not always expose deletions made by other users. This report counts only deletion events the connected account can actually observe.")

async def stats_cmd(update, context):
    if not is_admin(update): return
    await update.message.reply_text(await stats_text())

async def groups_view(q):
    rows = db.execute("SELECT chat_id,title,enabled FROM destinations ORDER BY title").fetchall()
    if not rows:
        return await q.edit_message_text("No groups loaded yet. Tap Load groups below.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Load groups", callback_data="loadgroups")],[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    kb = []
    for cid, title, enabled in rows[:80]:
        label = ("✅ " if enabled else "▫️ ") + title[:36]
        kb.append([InlineKeyboardButton(label, callback_data=f"toggle:{cid}")])
    kb += [[InlineKeyboardButton("🔄 Refresh from Telegram", callback_data="loadgroups")],
           [InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]
    await q.edit_message_text("👥 Groups/channels found in the connected account. Tap to select/deselect destinations. Only choose chats where you have permission to post.", reply_markup=InlineKeyboardMarkup(kb))

async def load_groups(q):
    await q.edit_message_text("Loading Telegram dialogs…")
    found = 0
    try:
        async for d in tg.iter_dialogs():
            ent = d.entity
            if getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False) or getattr(ent, "gigagroup", False):
                db.execute("INSERT INTO destinations(chat_id,title,enabled) VALUES(?,?,0) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title", (int(d.id), d.name or str(d.id)))
                found += 1
        db.commit()
        await groups_view(q)
    except Exception as e:
        log.exception("load groups")
        await q.edit_message_text(f"Could not load groups: {type(e).__name__}: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return await q.edit_message_text("⛔ Admin only.")
    data = q.data
    if data == "menu": return await menu(q)
    if data == "stats": return await q.edit_message_text(await stats_text(), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    if data == "errors":
        rows = db.execute("SELECT ts,chat_title,status,detail FROM events WHERE status IN ('failed','deleted') ORDER BY id DESC LIMIT 12").fetchall()
        if not rows: body = "No recorded failures/deletion events yet."
        else:
            body = "\\n\\n".join(f"{datetime.fromtimestamp(r[0],TZ).strftime('%d %b %H:%M')} · {r[1]} · {r[2]}\\n{r[3]}" for r in rows)
        return await q.edit_message_text("🔎 RECENT ERRORS / EVENTS\\n\\n"+body, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="errors")],[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    if data == "groups": return await groups_view(q)
    if data == "loadgroups": return await load_groups(q)
    if data.startswith("toggle:"):
        cid = int(data.split(":")[1])
        row = db.execute("SELECT enabled FROM destinations WHERE chat_id=?", (cid,)).fetchone()
        if row:
            db.execute("UPDATE destinations SET enabled=? WHERE chat_id=?", (0 if row[0] else 1, cid)); db.commit()
        return await groups_view(q)
    if data == "setmsg":
        context.user_data["awaiting_message"] = True
        return await q.edit_message_text("Send the message you want to schedule as your next chat message. Text and links are supported. For media/stickers, this starter version needs a media-specific upload flow.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    if data == "start":
        if not get_setting("message", ""): return await q.edit_message_text("Set a message first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Set Message",callback_data="setmsg")],[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
        if not enabled_destinations(): return await q.edit_message_text("Select at least one group first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 Manage Groups",callback_data="groups")],[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
        set_setting("running","1")
        return await q.edit_message_text(f"▶️ Schedule started. Interval: {get_setting('interval','10')} min.\\nUse /interval 10 to change it (minimum 2 minutes).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏸ Stop",callback_data="stop")],[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    if data == "stop":
        set_setting("running","0")
        return await q.edit_message_text("⏸ Schedule stopped.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))
    if data == "test":
        return await send_batch(context, test=True, reply_target=q.message)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if context.user_data.get("awaiting_message"):
        msg = update.effective_message
        if not msg.text:
            return await msg.reply_text("Please send text/link for now; media upload support can be added next.")
        set_setting("message", msg.text)
        context.user_data["awaiting_message"] = False
        await msg.reply_text("✅ Message saved. Use /interval 10 to set timing, then Start Schedule.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu",callback_data="menu")]]))

async def interval_cmd(update, context):
    if not is_admin(update): return
    try: mins = int(context.args[0])
    except Exception: return await update.message.reply_text("Usage: /interval 10  (minimum 2 minutes)")
    if mins < 2 or mins > 10080: return await update.message.reply_text("Choose between 2 and 10080 minutes.")
    set_setting("interval", mins)
    await update.message.reply_text(f"Interval saved: {mins} minutes.")

async def send_batch(context, test=False, reply_target=None):
    msg = get_setting("message","")
    destinations = enabled_destinations()
    if not msg or not destinations:
        text = "Set a message and select groups first."
        if reply_target: return await reply_target.reply_text(text)
        return
    if test: destinations = destinations[:1]
    success = failed = 0
    for cid, title in destinations:
        try:
            sent = await tg.send_message(int(cid), msg, link_preview=True)
            log_event(cid, title, "success", "Sent successfully", sent.id)
            success += 1
            await asyncio.sleep(2)
        except FloodWaitError as e:
            log_event(cid, title, "failed", f"Telegram FloodWait: wait {e.seconds}s")
            failed += 1
            await asyncio.sleep(min(e.seconds, 60))
        except Exception as e:
            log_event(cid, title, "failed", f"{type(e).__name__}: {e}")
            failed += 1
            log.exception("send failed to %s", title)
    text = f"{'🧪 TEST' if test else '📨 SEND RUN'}\\n✅ Sent: {success}\\n❌ Failed: {failed}\\nUse Today's Analytics / Error Report for details."
    if reply_target: await reply_target.reply_text(text)

async def scheduler(app: Application):
    while True:
        try:
            if get_setting("running","0") == "1":
                await send_batch(app)
                await asyncio.sleep(max(2, int(get_setting("interval","10"))) * 60)
            else:
                await asyncio.sleep(3)
        except Exception:
            log.exception("scheduler loop error")
            await asyncio.sleep(5)

@tg.on(events.MessageDeleted())
async def on_message_deleted(event):
    # Telegram only emits deletion updates for chats/channels the account can observe.
    try:
        ids = list(event.deleted_ids or [])
        for mid in ids:
            row = db.execute("SELECT chat_id,chat_title FROM events WHERE message_id=? AND status='success' ORDER BY id DESC LIMIT 1", (int(mid),)).fetchone()
            if row:
                log_event(row[0], row[1], "deleted", f"Telegram deletion update received for message ID {mid}", int(mid))
    except Exception:
        log.exception("Could not record deletion update")

async def post_init(app):
    await tg.start()
    me = await tg.get_me()
    log.info("Telegram user session connected as %s", getattr(me, "username", None) or me.id)
    app.create_task(scheduler(app))

async def post_shutdown(app):
    await tg.disconnect()
    db.close()

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("interval", interval_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
