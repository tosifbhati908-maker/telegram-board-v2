import os, asyncio, logging, sqlite3, time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

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


def nav_buttons(done=None, next_action=None, back_action="menu"):
    row = []
    if done:
        row.append(InlineKeyboardButton("✅ Done", callback_data=done))
    if next_action:
        row.append(InlineKeyboardButton("➡️ Next", callback_data=next_action))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data=back_action),
                 InlineKeyboardButton("🏠 Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


async def menu(update_or_query, text="📊 TELEGRAM BOARD V2\nChoose what you want to do:"):
    kb = [
        [InlineKeyboardButton("📈 Today's Analytics", callback_data="stats")],
        [InlineKeyboardButton("👥 Load / Manage Groups", callback_data="groups")],
        [InlineKeyboardButton("✉️ Set Message", callback_data="setmsg"),
         InlineKeyboardButton("⏱ Set Interval", callback_data="interval")],
        [InlineKeyboardButton("🎯 Daily Target", callback_data="target")],
        [InlineKeyboardButton("▶️ Start Schedule", callback_data="start"),
         InlineKeyboardButton("⏸ Stop Schedule", callback_data="stop")],
        [InlineKeyboardButton("🧪 Send Test", callback_data="test")]
    ]
    markup = InlineKeyboardMarkup(kb)
    if hasattr(update_or_query, "edit_message_text"):
        try:
            await update_or_query.edit_message_text(text, reply_markup=markup)
        except Exception:
            if getattr(update_or_query, "message", None):
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
    counts = {k: v for k, v in rows}
    attempts = counts.get("success", 0) + counts.get("failed", 0)
    target = int(get_setting("daily_target", "0") or 0)
    remaining = max(0, target - counts.get("success", 0)) if target else None
    target_line = f"🎯 Daily target: {target}\n⏳ Remaining to target: {remaining}" if target else "🎯 Daily target: Not set (use Daily Target)"
    return (
        f"📈 TODAY'S PROGRESS · {now.strftime('%d %b %Y')}\n\n"
        f"📨 Total attempts: {attempts}\n"
        f"✅ Sent successfully: {counts.get('success', 0)}\n"
        f"❌ Failed: {counts.get('failed', 0)}\n"
        f"👥 Selected groups: {len(enabled_destinations())}\n"
        f"⏱ Schedule: {'RUNNING' if get_setting('running', '0') == '1' else 'STOPPED'}\n"
        f"🔁 Interval: {get_setting('interval', '10')} minutes\n"
        f"{target_line}"
    )


async def groups_view(q):
    rows = db.execute("SELECT chat_id,title,enabled FROM destinations ORDER BY title").fetchall()
    if not rows:
        return await q.edit_message_text(
            "👥 No groups saved yet. Tap Refresh to load groups from your Telegram account.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh Groups", callback_data="loadgroups")],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
            ])
        )
    kb = []
    for cid, title, enabled in rows[:80]:
        label = ("✅ " if enabled else "▫️ ") + title[:40]
        kb.append([InlineKeyboardButton(label, callback_data=f"toggle:{cid}")])
    kb.extend([
        [InlineKeyboardButton("🔄 Refresh Groups", callback_data="loadgroups")],
        [InlineKeyboardButton("✅ Done — Groups Selected", callback_data="groups_done")],
        [InlineKeyboardButton("➡️ Next — Set Message", callback_data="setmsg")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]
    ])
    await q.edit_message_text(
        "👥 GROUPS ONLY\nTap a group to select/deselect it. Channels are excluded.\n\n"
        "Choose only groups where your connected Telegram account can send messages.",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def load_groups(q):
    await q.edit_message_text("🔄 Loading groups from your connected Telegram account…")
    found_ids = set()
    try:
        async for d in tg.iter_dialogs():
            ent = d.entity
            # Only actual groups: basic groups and supergroups. Exclude broadcast channels.
            is_group = bool(getattr(d, "is_group", False)) or bool(getattr(ent, "megagroup", False))
            is_channel = bool(getattr(ent, "broadcast", False))
            if is_group and not is_channel:
                cid = int(d.id)
                found_ids.add(cid)
                db.execute(
                    "INSERT INTO destinations(chat_id,title,enabled) VALUES(?,?,0) "
                    "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
                    (cid, d.name or str(cid))
                )
        # Remove saved destinations that are no longer recognised as groups; prevents old channels appearing.
        existing = db.execute("SELECT chat_id FROM destinations").fetchall()
        for (cid,) in existing:
            if int(cid) not in found_ids:
                db.execute("DELETE FROM destinations WHERE chat_id=?", (cid,))
        db.commit()
        await groups_view(q)
    except Exception as e:
        log.exception("load groups")
        await q.edit_message_text(
            f"Could not load groups: {type(e).__name__}\nCheck the connected account and try again.",
            reply_markup=nav_buttons(back_action="groups")
        )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return await q.edit_message_text("⛔ Admin only.")
    data = q.data

    if data == "menu":
        return await menu(q)
    if data == "stats":
        return await q.edit_message_text(await stats_text(), reply_markup=nav_buttons(done="stats_done", back_action="menu"))
    if data == "stats_done":
        return await menu(q, "✅ Analytics checked. What would you like to do next?")
    if data == "msg_done":
        context.user_data.pop("awaiting_message", None)
        return await menu(q, "✅ Message step finished. Choose the next action.")
    if data == "test_done":
        return await menu(q, "✅ Test finished. Choose the next action.")
    if data == "groups":
        return await groups_view(q)
    if data == "loadgroups":
        return await load_groups(q)
    if data.startswith("toggle:"):
        cid = int(data.split(":", 1)[1])
        row = db.execute("SELECT enabled FROM destinations WHERE chat_id=?", (cid,)).fetchone()
        if row:
            db.execute("UPDATE destinations SET enabled=? WHERE chat_id=?", (0 if row[0] else 1, cid))
            db.commit()
        return await groups_view(q)
    if data == "groups_done":
        return await q.edit_message_text(
            f"✅ Groups selection saved. Selected groups: {len(enabled_destinations())}",
            reply_markup=nav_buttons(done="groups_done_finish", next_action="setmsg", back_action="groups")
        )
    if data == "groups_done_finish":
        return await menu(q, "✅ Done. Groups are saved. Choose your next step.")
    if data == "setmsg":
        context.user_data["awaiting_message"] = True
        return await q.edit_message_text(
            "✉️ STEP 1 — SET MESSAGE\nSend the text or link you want to schedule.\n\nAfter saving, use Next to set the interval or Back/Menu to leave.",
            reply_markup=nav_buttons(done="cancel_input", back_action="groups")
        )
    if data == "cancel_input":
        context.user_data.pop("awaiting_message", None)
        context.user_data.pop("awaiting_interval", None)
        context.user_data.pop("awaiting_target", None)
        return await menu(q, "↩️ Input cancelled. Choose the next action.")
    if data == "interval":
        context.user_data["awaiting_interval"] = True
        return await q.edit_message_text(
            f"⏱ STEP 2 — SET INTERVAL\nCurrent interval: {get_setting('interval', '10')} minutes.\nSend the new interval in minutes (minimum 2).",
            reply_markup=nav_buttons(done="interval_done", back_action="setmsg")
        )
    if data == "interval_done":
        context.user_data.pop("awaiting_interval", None)
        return await menu(q, "✅ Interval step finished. Choose your next action.")
    if data == "target":
        context.user_data["awaiting_target"] = True
        return await q.edit_message_text(
            f"🎯 DAILY TARGET\nCurrent target: {get_setting('daily_target', 'Not set')} successful messages per day.\nSend a whole number, e.g. 100. Send 0 to remove the target.",
            reply_markup=nav_buttons(done="target_done", back_action="menu")
        )
    if data == "target_done":
        context.user_data.pop("awaiting_target", None)
        return await menu(q, "✅ Daily target step finished.")
    if data == "start":
        if not get_setting("message", ""):
            return await q.edit_message_text("✉️ Set a message first.", reply_markup=nav_buttons(done="cancel_input", next_action="setmsg", back_action="menu"))
        if not enabled_destinations():
            return await q.edit_message_text("👥 Select at least one group first.", reply_markup=nav_buttons(done="groups_done_finish", next_action="groups", back_action="menu"))
        set_setting("running", "1")
        return await q.edit_message_text(
            f"▶️ Schedule started\nInterval: {get_setting('interval', '10')} minutes\nGroups selected: {len(enabled_destinations())}",
            reply_markup=nav_buttons(done="start_done", next_action="stats", back_action="menu")
        )
    if data == "start_done":
        return await menu(q, "✅ Schedule is running. Choose your next action.")
    if data == "stop":
        set_setting("running", "0")
        return await q.edit_message_text("⏸ Schedule stopped.", reply_markup=nav_buttons(done="stop_done", next_action="stats", back_action="menu"))
    if data == "stop_done":
        return await menu(q, "✅ Schedule stopped. Choose your next action.")
    if data == "test":
        return await send_batch(context, test=True, reply_target=q.message)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    msg = update.effective_message
    text = (msg.text or "").strip()

    if context.user_data.get("awaiting_message"):
        if not text:
            return await msg.reply_text("Please send text or a link for now. Media/stickers need a separate upload flow.", reply_markup=nav_buttons(done="cancel_input", back_action="menu"))
        set_setting("message", text)
        context.user_data["awaiting_message"] = False
        return await msg.reply_text(
            "✅ Message saved successfully.\nNext step: set the interval, then start the schedule.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ Next — Set Interval", callback_data="interval")],
                [InlineKeyboardButton("▶️ Start Schedule", callback_data="start")],
                [InlineKeyboardButton("✅ Done", callback_data="msg_done")],
                [InlineKeyboardButton("⬅️ Back", callback_data="groups"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]
            ])
        )
    if context.user_data.get("awaiting_interval"):
        try:
            mins = int(text)
            if mins < 2 or mins > 10080:
                raise ValueError
        except ValueError:
            return await msg.reply_text("❌ Enter a whole number from 2 to 10080 minutes.", reply_markup=nav_buttons(done="interval_done", back_action="menu"))
        set_setting("interval", mins)
        context.user_data["awaiting_interval"] = False
        return await msg.reply_text(
            f"✅ Interval saved: {mins} minutes.\nNext, start the schedule when ready.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ Next — Start Schedule", callback_data="start")],
                [InlineKeyboardButton("📈 Today's Analytics", callback_data="stats")],
                [InlineKeyboardButton("✅ Done", callback_data="interval_done")],
                [InlineKeyboardButton("⬅️ Back", callback_data="setmsg"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]
            ])
        )
    if context.user_data.get("awaiting_target"):
        try:
            target = int(text)
            if target < 0:
                raise ValueError
        except ValueError:
            return await msg.reply_text("❌ Enter a whole number 0 or higher.", reply_markup=nav_buttons(done="target_done", back_action="menu"))
        set_setting("daily_target", target)
        context.user_data["awaiting_target"] = False
        target_text = "Daily target removed." if target == 0 else f"Daily target saved: {target} successful messages/day."
        return await msg.reply_text("✅ " + target_text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📈 Today's Analytics", callback_data="stats")],
            [InlineKeyboardButton("✅ Done", callback_data="target_done")],
            [InlineKeyboardButton("🏠 Menu", callback_data="menu")]
        ]))


async def stats_cmd(update, context):
    if not is_admin(update):
        return
    await update.message.reply_text(await stats_text(), reply_markup=nav_buttons(done="stats_done", back_action="menu"))


async def interval_cmd(update, context):
    if not is_admin(update):
        return
    try:
        mins = int(context.args[0])
    except Exception:
        return await update.message.reply_text("Usage: /interval 10 (minimum 2 minutes)", reply_markup=nav_buttons(back_action="menu"))
    if mins < 2 or mins > 10080:
        return await update.message.reply_text("Choose between 2 and 10080 minutes.", reply_markup=nav_buttons(back_action="menu"))
    set_setting("interval", mins)
    await update.message.reply_text(f"✅ Interval saved: {mins} minutes.", reply_markup=nav_buttons(done="interval_done", next_action="start", back_action="menu"))


async def send_batch(context, test=False, reply_target=None):
    msg = get_setting("message", "")
    destinations = enabled_destinations()
    if not msg or not destinations:
        text = "Set a message and select groups first."
        if reply_target:
            return await reply_target.reply_text(text, reply_markup=nav_buttons(done="cancel_input", next_action="groups", back_action="menu"))
        return
    if test:
        destinations = destinations[:1]
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
    text = f"{'🧪 TEST RESULT' if test else '📨 SEND RESULT'}\n\n✅ Sent: {success}\n❌ Failed: {failed}\n\nTap Done when finished, or open Analytics to see today's totals."
    if reply_target:
        await reply_target.reply_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Done", callback_data="test_done")],
            [InlineKeyboardButton("📈 Today's Analytics", callback_data="stats")],
            [InlineKeyboardButton("➡️ Next — Send Test Again", callback_data="test")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu"), InlineKeyboardButton("🏠 Menu", callback_data="menu")]
        ]))


async def scheduler(app: Application):
    while True:
        try:
            if get_setting("running", "0") == "1":
                await send_batch(app)
                await asyncio.sleep(max(2, int(get_setting("interval", "10"))) * 60)
            else:
                await asyncio.sleep(3)
        except Exception:
            log.exception("scheduler loop error")
            await asyncio.sleep(5)


@tg.on(events.MessageDeleted())
async def on_message_deleted(event):
    try:
        for mid in list(event.deleted_ids or []):
            row = db.execute("SELECT chat_id,chat_title FROM events WHERE message_id=? AND status='success' ORDER BY id DESC LIMIT 1", (int(mid),)).fetchone()
            if row:
                log_event(row[0], row[1], "deleted", f"Deletion update received for message ID {mid}", int(mid))
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
