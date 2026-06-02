from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from functools import wraps
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, BadPassword
import json
import os
import threading
import time
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "instagram_chatbot_secret_2024"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "avazbekdeveloper"

DATA_FILE = "data.json"


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated
bots = {}
lock = threading.Lock()


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"accounts": {}, "keywords": {}}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_or_create_account_data(data, username):
    if username not in data["accounts"]:
        data["accounts"][username] = {
            "username": username, "password": "",
            "active": False, "status": "offline",
            "last_check": None, "messages_replied": 0, "log": []
        }
    if username not in data["keywords"]:
        data["keywords"][username] = []
    return data


def add_log(username, msg):
    data = load_data()
    if username in data["accounts"]:
        logs = data["accounts"][username].get("log", [])
        ts = datetime.now().strftime("%H:%M:%S")
        logs.append(f"[{ts}] {msg}")
        data["accounts"][username]["log"] = logs[-30:]  # oxirgi 30 ta
        save_data(data)
    log.info(f"[{username}] {msg}")


def bot_worker(username):
    data = load_data()
    account = data["accounts"].get(username)
    if not account:
        return

    password = account["password"]
    cl = Client()
    cl.delay_range = [3, 7]
    session_file = f"session_{username}.json"

    add_log(username, "Login qilinmoqda...")

    try:
        if os.path.exists(session_file):
            cl.load_settings(session_file)
        cl.login(username, password)
        cl.dump_settings(session_file)
        add_log(username, f"Login muvaffaqiyatli! User ID: {cl.user_id}")
    except BadPassword:
        add_log(username, "XATO: Parol noto'g'ri!")
        _set_status(username, "Xato: Parol noto'g'ri")
        return
    except Exception as e:
        err = str(e)[:80]
        add_log(username, f"XATO login: {err}")
        _set_status(username, f"Xato: {err[:50]}")
        return

    with lock:
        bots[username] = {"client": cl, "running": True}
    _set_status(username, "online")
    add_log(username, "Bot ishga tushdi. DM va Commentlar tekshirilmoqda...")

    # DM uchun
    replied_file = f"replied_{username}.json"
    replied_messages = set()
    if os.path.exists(replied_file):
        with open(replied_file, "r") as f:
            replied_messages = set(json.load(f))

    # Comment uchun
    replied_comments_file = f"replied_comments_{username}.json"
    replied_comments = set()
    if os.path.exists(replied_comments_file):
        with open(replied_comments_file, "r") as f:
            replied_comments = set(json.load(f))

    # Media ID larni cache qilish
    cached_media_ids = []
    media_cache_time = 0

    while True:
        with lock:
            if not bots.get(username, {}).get("running"):
                break

        try:
            data = load_data()
            keywords = data.get("keywords", {}).get(username, [])

            if not keywords:
                add_log(username, "Kalit so'zlar yo'q, 30s kutilmoqda...")
                time.sleep(30)
                continue

            now = datetime.now().strftime("%H:%M:%S")

            # ── DM tekshirish ──────────────────────────────────────
            threads = cl.direct_threads(amount=20, selected_filter="unread")
            if not threads:
                threads = cl.direct_threads(amount=20)

            add_log(username, f"DM: {len(threads)} ta chat topildi")

            for thread in threads:
                if not thread.messages:
                    continue
                last_msg = thread.messages[0]
                msg_id = str(last_msg.id)
                msg_text = (getattr(last_msg, "text", "") or "").strip()
                sender_id = str(last_msg.user_id)
                my_id = str(cl.user_id)

                if sender_id == my_id or msg_id in replied_messages or not msg_text:
                    continue

                msg_lower = msg_text.lower()
                add_log(username, f"DM yangi: '{msg_text[:40]}'")

                matched = False
                for kw in keywords:
                    if kw["keyword"].lower() in msg_lower:
                        reply_text = kw.get("dm_text") or kw.get("reply", "")
                        if not reply_text:
                            break
                        add_log(username, f"DM mos: '{kw['keyword']}' → javob yuborilmoqda...")
                        try:
                            cl.direct_send(reply_text, thread_ids=[thread.id])
                            replied_messages.add(msg_id)
                            with open(replied_file, "w") as f:
                                json.dump(list(replied_messages), f)
                            data2 = load_data()
                            if username in data2["accounts"]:
                                data2["accounts"][username]["messages_replied"] = \
                                    data2["accounts"][username].get("messages_replied", 0) + 1
                                save_data(data2)
                            add_log(username, f"DM javob yuborildi: '{reply_text[:40]}'")
                            matched = True
                        except Exception as e2:
                            add_log(username, f"DM javob xato: {e2}")
                        break

                if not matched:
                    replied_messages.add(msg_id)

            # ── Comment tekshirish (har 30 soniyada) ──────────────
            comment_check_interval = 30
            if not hasattr(bot_worker, '_last_comment_check'):
                pass
            now_ts = time.time()
            last_comment_check = bots.get(username, {}).get("last_comment_check", 0)
            if now_ts - last_comment_check < comment_check_interval:
                data3 = load_data()
                if username in data3["accounts"]:
                    data3["accounts"][username]["last_check"] = now
                    save_data(data3)
                time.sleep(10)
                continue

            with lock:
                if username in bots:
                    bots[username]["last_comment_check"] = now_ts

            # Har 10 daqiqada media listni yangilaymiz
            if time.time() - media_cache_time > 600:
                try:
                    medias = cl.user_medias(cl.user_id, amount=5)
                    cached_media_ids = [str(m.id) for m in medias]
                    media_cache_time = time.time()
                    add_log(username, f"Comment: {len(cached_media_ids)} ta post topildi")
                except Exception as e:
                    add_log(username, f"Post olishda xato: {str(e)[:60]}")

            for media_id in cached_media_ids:
                with lock:
                    if not bots.get(username, {}).get("running"):
                        break
                try:
                    time.sleep(2)  # postlar orasida pauza
                    comments = cl.media_comments(media_id, amount=20)
                except Exception:
                    continue

                for comment in comments:
                    comment_id = str(comment.pk)
                    comment_text = (getattr(comment, "text", "") or "").strip()
                    commenter_id = str(comment.user.pk)
                    my_id = str(cl.user_id)

                    if commenter_id == my_id:
                        continue
                    if comment_id in replied_comments:
                        continue
                    if not comment_text:
                        continue

                    comment_lower = comment_text.lower()

                    for kw in keywords:
                        if kw["keyword"].lower() not in comment_lower:
                            continue

                        add_log(username, f"Comment mos: '{comment_text[:40]}' → @{comment.user.username}")

                        # Comment ga javob
                        comment_reply = kw.get("comment_reply", "")
                        if comment_reply:
                            try:
                                cl.media_comment(media_id, comment_reply,
                                                 replied_to_comment_id=comment_id)
                                add_log(username, f"Comment javob: '{comment_reply[:40]}'")
                            except Exception as e2:
                                add_log(username, f"Comment reply xato: {str(e2)[:60]}")

                        # DM yuborish
                        dm_text = kw.get("dm_text") or kw.get("reply", "")
                        if dm_text:
                            try:
                                cl.direct_send(dm_text, user_ids=[commenter_id])
                                add_log(username, f"DM yuborildi @{comment.user.username}: '{dm_text[:40]}'")
                            except Exception as e3:
                                add_log(username, f"DM xato: {str(e3)[:60]}")

                        replied_comments.add(comment_id)
                        with open(replied_comments_file, "w") as f:
                            json.dump(list(replied_comments), f)

                        data2 = load_data()
                        if username in data2["accounts"]:
                            data2["accounts"][username]["messages_replied"] = \
                                data2["accounts"][username].get("messages_replied", 0) + 1
                            save_data(data2)
                        break

                    replied_comments.add(comment_id)

            data3 = load_data()
            if username in data3["accounts"]:
                data3["accounts"][username]["last_check"] = now
                save_data(data3)

        except LoginRequired:
            add_log(username, "Session tugadi, qayta login...")
            try:
                cl.login(username, password)
                cl.dump_settings(session_file)
                add_log(username, "Qayta login muvaffaqiyatli")
            except Exception as e:
                add_log(username, f"Qayta login xato: {e}")
                break
        except Exception as e:
            add_log(username, f"Loop xato: {str(e)[:60]}")
            time.sleep(15)
            continue

        time.sleep(10)

    with lock:
        if username in bots:
            del bots[username]
    _set_status(username, "offline")
    add_log(username, "Bot to'xtatildi.")


def _set_status(username, status):
    data = load_data()
    if username in data["accounts"]:
        data["accounts"][username]["status"] = status
        data["accounts"][username]["active"] = (status == "online")
        save_data(data)


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Login yoki parol noto'g'ri!"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    data = load_data()
    return render_template("index.html", accounts=data["accounts"], keywords=data["keywords"])


@app.route("/add_account", methods=["POST"])
@login_required
def add_account():
    username = request.form.get("username", "").strip().lstrip("@")
    password = request.form.get("password", "").strip()
    if not username or not password:
        flash("Username va parol kiritish shart!", "error")
        return redirect(url_for("index"))
    data = load_data()
    get_or_create_account_data(data, username)
    data["accounts"][username]["password"] = password
    save_data(data)
    flash(f"@{username} account qo'shildi!", "success")
    return redirect(url_for("index"))


@app.route("/delete_account/<username>")
@login_required
def delete_account(username):
    with lock:
        if username in bots:
            bots[username]["running"] = False
    data = load_data()
    data["accounts"].pop(username, None)
    data["keywords"].pop(username, None)
    save_data(data)
    for f in [f"session_{username}.json", f"replied_{username}.json"]:
        if os.path.exists(f):
            os.remove(f)
    flash(f"@{username} o'chirildi.", "success")
    return redirect(url_for("index"))


@app.route("/start/<username>")
@login_required
def start_bot(username):
    data = load_data()
    if username not in data["accounts"]:
        flash("Account topilmadi!", "error")
        return redirect(url_for("index"))
    with lock:
        if username in bots and bots[username].get("running"):
            flash(f"@{username} allaqachon ishlamoqda.", "info")
            return redirect(url_for("index"))

    _set_status(username, "ulanmoqda...")
    t = threading.Thread(target=bot_worker, args=(username,), daemon=True)
    t.start()
    flash(f"@{username} boti ishga tushirildi!", "success")
    return redirect(url_for("index"))


@app.route("/stop/<username>")
@login_required
def stop_bot(username):
    with lock:
        if username in bots:
            bots[username]["running"] = False
    _set_status(username, "to'xtatildi")
    flash(f"@{username} boti to'xtatildi.", "success")
    return redirect(url_for("index"))


@app.route("/keywords/<username>")
@login_required
def keywords_page(username):
    data = load_data()
    if username not in data["accounts"]:
        flash("Account topilmadi!", "error")
        return redirect(url_for("index"))
    return render_template("keywords.html",
        username=username,
        keywords=data["keywords"].get(username, []),
        account=data["accounts"][username])


@app.route("/add_keyword/<username>", methods=["POST"])
@login_required
def add_keyword(username):
    keyword = request.form.get("keyword", "").strip()
    comment_reply = request.form.get("comment_reply", "").strip()
    dm_text = request.form.get("dm_text", "").strip()
    if not keyword or (not comment_reply and not dm_text):
        flash("Kalit so'z va kamida bitta javob (comment yoki DM) kiritish shart!", "error")
        return redirect(url_for("keywords_page", username=username))
    data = load_data()
    if username not in data["keywords"]:
        data["keywords"][username] = []
    for kw in data["keywords"][username]:
        if kw["keyword"].lower() == keyword.lower():
            flash("Bu kalit so'z allaqachon mavjud!", "error")
            return redirect(url_for("keywords_page", username=username))
    data["keywords"][username].append({
        "keyword": keyword,
        "comment_reply": comment_reply,
        "dm_text": dm_text,
        "reply": dm_text or comment_reply  # orqaga moslik uchun
    })
    save_data(data)
    flash(f'"{keyword}" kalit so\'zi qo\'shildi!', "success")
    return redirect(url_for("keywords_page", username=username))


@app.route("/delete_keyword/<username>/<int:index>")
@login_required
def delete_keyword(username, index):
    data = load_data()
    kws = data["keywords"].get(username, [])
    if 0 <= index < len(kws):
        removed = kws.pop(index)
        data["keywords"][username] = kws
        save_data(data)
        flash(f'"{removed["keyword"]}" o\'chirildi.', "success")
    return redirect(url_for("keywords_page", username=username))


@app.route("/logs/<username>")
@login_required
def logs_page(username):
    data = load_data()
    if username not in data["accounts"]:
        flash("Account topilmadi!", "error")
        return redirect(url_for("index"))
    logs = data["accounts"][username].get("log", [])
    return render_template("logs.html",
        username=username,
        logs=logs,
        account=data["accounts"][username])


@app.route("/api/status")
@login_required
def api_status():
    data = load_data()
    return jsonify(data["accounts"])


@app.route("/api/logs/<username>")
@login_required
def api_logs(username):
    data = load_data()
    logs = data.get("accounts", {}).get(username, {}).get("log", [])
    return jsonify(logs)


def bulk_comment_reply_worker(username, reply_text, password):
    cl = None
    with lock:
        if username in bots:
            cl = bots[username]["client"]

    if cl is None:
        cl = Client()
        cl.delay_range = [2, 5]
        session_file = f"session_{username}.json"
        try:
            if os.path.exists(session_file):
                cl.load_settings(session_file)
            cl.login(username, password)
            cl.dump_settings(session_file)
        except Exception as e:
            add_log(username, f"Bulk reply: login xato: {str(e)[:60]}")
            return

    replied_comments_file = f"replied_comments_{username}.json"
    replied_comments = set()
    if os.path.exists(replied_comments_file):
        with open(replied_comments_file, "r") as f:
            replied_comments = set(json.load(f))

    sent = 0
    try:
        medias = cl.user_medias(cl.user_id, amount=10)
        add_log(username, f"Bulk reply: {len(medias)} ta post topildi")
        for media in medias:
            media_id = str(media.id)
            try:
                comments = cl.media_comments(media_id, amount=50)
            except Exception as e:
                add_log(username, f"Bulk reply: comment olishda xato: {str(e)[:50]}")
                continue
            for comment in comments:
                comment_id = str(comment.pk)
                commenter_id = str(comment.user.pk)
                if commenter_id == str(cl.user_id):
                    continue
                if comment_id in replied_comments:
                    continue
                try:
                    time.sleep(3)
                    cl.media_comment(media_id, reply_text, replied_to_comment_id=comment_id)
                    replied_comments.add(comment_id)
                    sent += 1
                    add_log(username, f"Bulk reply: @{comment.user.username} ga yuborildi ({sent})")
                except Exception as e:
                    add_log(username, f"Bulk reply xato: {str(e)[:60]}")

        with open(replied_comments_file, "w") as f:
            json.dump(list(replied_comments), f)
        add_log(username, f"Bulk reply tugadi. Jami {sent} ta commentga javob berildi.")
    except Exception as e:
        add_log(username, f"Bulk reply umumiy xato: {str(e)[:60]}")


@app.route("/bulk_comment_reply/<username>", methods=["POST"])
@login_required
def bulk_comment_reply(username):
    reply_text = request.form.get("reply_text", "").strip()
    if not reply_text:
        flash("Javob matni kiritish shart!", "error")
        return redirect(url_for("keywords_page", username=username))
    data = load_data()
    account = data["accounts"].get(username)
    if not account:
        flash("Account topilmadi!", "error")
        return redirect(url_for("index"))
    password = account.get("password", "")
    t = threading.Thread(
        target=bulk_comment_reply_worker,
        args=(username, reply_text, password),
        daemon=True
    )
    t.start()
    flash(f"Bulk reply ishga tushdi! Loglardan kuzatib boring.", "success")
    return redirect(url_for("keywords_page", username=username))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
