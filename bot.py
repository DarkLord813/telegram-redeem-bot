import os
import random
import sqlite3
import requests
import threading
import time
import base64
import string
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

# ================= ENV =================
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
GITHUB_FILE_PATH = "pulse_profit.db"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ================= ADMINS =================
ADMIN_IDS = [7475473197, 7713987088]  # Replace with your admin IDs

# ================= REQUIRED CHANNEL =================
REQUIRED_CHANNEL = "@PulseProfit012"
CHANNEL_LINK = "https://t.me/PulseProfit012"

# ================= SETTINGS =================
COOLDOWN_TIME = 60
WITHDRAWAL_COOLDOWN = 3600
MIN_WITHDRAW = 50
MAX_DAILY_WITHDRAW = 500

STAR_PACKAGES = {
    "10": 10,
    "50": 45,
    "100": 85,
    "500": 400,
    "1000": 750
}

# ================= DATABASE =================
conn = sqlite3.connect("pulse_profit.db", check_same_thread=False)
cursor = conn.cursor()

# Create essential tables
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_channel INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users_wallet (
    user_id INTEGER PRIMARY KEY,
    stars INTEGER DEFAULT 0,
    total_earned INTEGER DEFAULT 0,
    referrals INTEGER DEFAULT 0,
    premium INTEGER DEFAULT 0,
    tasks_done INTEGER DEFAULT 0,
    daily_withdrawn INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    referrer_id INTEGER,
    referred_id INTEGER UNIQUE
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS withdraw_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    withdrawal_type TEXT DEFAULT 'admin',
    status TEXT DEFAULT 'pending',
    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_actions (
    user_id INTEGER,
    action_type TEXT,
    action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    task_type TEXT,
    task_data TEXT,
    reward INTEGER,
    active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    task_id INTEGER,
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verified INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS redeem_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    amount INTEGER,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS redeemed_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id INTEGER,
    user_id INTEGER,
    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS admin_sessions (
    admin_id INTEGER PRIMARY KEY,
    session_data TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS backup_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backup_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    backup_type TEXT,
    status TEXT,
    details TEXT
)
""")

conn.commit()

# ================= KEEP-ALIVE SERVICE =================
class KeepAliveService:
    def __init__(self, health_url=None):
        self.health_url = health_url
        self.is_running = False
        self.ping_count = 0
        
    def start(self):
        self.is_running = True
        def ping_loop():
            while self.is_running:
                try:
                    self.ping_count += 1
                    if self.health_url:
                        requests.get(self.health_url, timeout=15)
                        print(f"✅ Keep-alive ping #{self.ping_count}")
                    time.sleep(240)
                except:
                    time.sleep(60)
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        print("🔄 Keep-alive service started")

keep_alive = KeepAliveService()

# ================= FLASK ENDPOINTS =================
@app.route('/')
def home():
    return jsonify({'status': 'running', 'service': 'Pulse Profit Bot'})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'pings': keep_alive.ping_count}), 200

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    try:
        update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
        bot.process_new_updates([update])
        return 'OK', 200
    except:
        return 'ERROR', 500

# ================= GITHUB BACKUP SYSTEM =================
def backup_to_github(backup_type="auto", details=""):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False
    try:
        with open("pulse_profit.db", "rb") as f:
            content = base64.b64encode(f.read()).decode()
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        r = requests.get(url, headers=headers)
        sha = None
        if r.status_code == 200:
            sha = r.json()["sha"]
        data = {
            "message": f"Backup {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {backup_type}",
            "content": content
        }
        if sha:
            data["sha"] = sha
        response = requests.put(url, json=data, headers=headers)
        if response.status_code in [200, 201]:
            cursor.execute("INSERT INTO backup_log (backup_type, status, details) VALUES (?,?,?)",
                          (backup_type, "success", details))
            conn.commit()
            return True
    except:
        pass
    return False

def backup_loop():
    while True:
        time.sleep(3600)
        backup_to_github("hourly", "Automatic hourly backup")

if GITHUB_TOKEN and GITHUB_REPO:
    threading.Thread(target=backup_loop, daemon=True).start()
    print("✅ GitHub backup system started")

# ================= HELPER FUNCTIONS =================
def get_wallet(user_id):
    cursor.execute("SELECT * FROM users_wallet WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users_wallet (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return get_wallet(user_id)
    return user

def add_stars(user_id, amount):
    cursor.execute("UPDATE users_wallet SET stars = stars + ?, total_earned = total_earned + ? WHERE user_id=?", 
                  (amount, amount, user_id))
    conn.commit()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user_name(user_id):
    try:
        user = bot.get_chat_member(user_id, user_id).user
        name = user.first_name
        if user.username:
            name += f" (@{user.username})"
        return name
    except:
        return f"User {user_id}"

def check_channel(user_id):
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def check_cooldown(user_id, action, seconds):
    cursor.execute("SELECT action_time FROM user_actions WHERE user_id=? AND action_type=? ORDER BY action_time DESC LIMIT 1", 
                  (user_id, action))
    last = cursor.fetchone()
    if last:
        last_time = datetime.strptime(last[0], '%Y-%m-%d %H:%M:%S')
        diff = (datetime.now() - last_time).total_seconds()
        if diff < seconds:
            return int(seconds - diff)
    return 0

def log_action(user_id, action):
    cursor.execute("INSERT INTO user_actions (user_id, action_type, action_time) VALUES (?, ?, ?)", 
                   (user_id, action, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()

def reset_daily_withdrawals():
    cursor.execute("UPDATE users_wallet SET daily_withdrawn = 0")
    conn.commit()
    print("✅ Daily withdrawal limits reset")
    if GITHUB_TOKEN and GITHUB_REPO:
        threading.Thread(target=backup_to_github, args=("daily_reset", "Daily limits reset"), daemon=True).start()

# ================= AUTO WITHDRAWAL PROCESSOR =================
def process_withdrawals():
    while True:
        time.sleep(300)
        cursor.execute("SELECT id, user_id, amount FROM withdraw_requests WHERE status='pending' AND withdrawal_type='stars'")
        pending = cursor.fetchall()
        for req_id, user_id, amount in pending:
            try:
                prices = [LabeledPrice(label=f"Withdrawal of {amount} Stars", amount=amount)]
                bot.send_invoice(
                    user_id,
                    title="Pulse Profit Withdrawal",
                    description=f"Your withdrawal of {amount} 🟡⭐ stars",
                    invoice_payload=f"withdraw_{req_id}",
                    provider_token="",
                    currency="XTR",
                    prices=prices,
                    start_parameter="withdraw"
                )
                cursor.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
                conn.commit()
            except:
                pass
        time.sleep(300)

threading.Thread(target=process_withdrawals, daemon=True).start()

# ================= MAIN MENU =================
def main_menu(user_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💰 EARN STARS", callback_data="earn"),
        InlineKeyboardButton("📋 TASKS", callback_data="show_tasks")
    )
    markup.row(
        InlineKeyboardButton("👥 REFER", callback_data="refer"),
        InlineKeyboardButton("👤 PROFILE", callback_data="profile")
    )
    markup.row(
        InlineKeyboardButton("🏆 LEADERBOARD", callback_data="leaderboard"),
        InlineKeyboardButton("💎 PREMIUM", callback_data="premium")
    )
    markup.row(
        InlineKeyboardButton("🟡 BUY STARS", callback_data="buy_menu"),
        InlineKeyboardButton("💳 WITHDRAW", callback_data="withdraw_menu")
    )
    markup.row(
        InlineKeyboardButton("🎫 REDEEM CODE", callback_data="redeem_menu")
    )
    if is_admin(user_id):
        markup.row(
            InlineKeyboardButton("👑 ADMIN PANEL", callback_data="admin_panel")
        )
    return markup

# ================= START COMMAND =================
@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name
    
    # Check for referral
    args = message.text.split()
    if len(args) > 1:
        try:
            referrer_id = int(args[1])
            if referrer_id != user_id:
                cursor.execute("SELECT * FROM referrals WHERE referred_id=?", (user_id,))
                if not cursor.fetchone():
                    cooldown = check_cooldown(referrer_id, "refer", COOLDOWN_TIME)
                    if cooldown == 0:
                        cursor.execute("INSERT INTO referrals VALUES (?,?)", (referrer_id, user_id))
                        cursor.execute("UPDATE users_wallet SET referrals = referrals + 1 WHERE user_id=?", (referrer_id,))
                        add_stars(referrer_id, 5)
                        log_action(referrer_id, "refer")
                        conn.commit()
                        try:
                            bot.send_message(referrer_id, f"🎉 You earned 5 🟡⭐ from a new referral!")
                        except:
                            pass
        except:
            pass
    
    # Check channel membership
    cursor.execute("SELECT joined_channel FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    
    if user and user[0] == 1:
        get_wallet(user_id)
        text = f"⚡ Welcome back to Pulse Profit!\n\n💰 Balance: {get_wallet(user_id)[1]} 🟡⭐"
        bot.send_message(user_id, text, reply_markup=main_menu(user_id))
    elif check_channel(user_id):
        cursor.execute("INSERT OR REPLACE INTO users (user_id, username, first_name, joined_channel) VALUES (?,?,?,1)", 
                      (user_id, username, first_name))
        conn.commit()
        get_wallet(user_id)
        text = f"⚡ Welcome to Pulse Profit!\n\n💰 Balance: 0 🟡⭐"
        bot.send_message(user_id, text, reply_markup=main_menu(user_id))
    else:
        text = f"""
🔒 CHANNEL REQUIRED

Please join our channel first:

📢 {REQUIRED_CHANNEL}
🔗 {CHANNEL_LINK}

After joining, click the button below.
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📢 JOIN", url=CHANNEL_LINK),
            InlineKeyboardButton("✅ VERIFY", callback_data="verify_channel")
        )
        bot.send_message(user_id, text, reply_markup=markup)

# ================= VERIFY CHANNEL =================
@bot.callback_query_handler(func=lambda c: c.data == "verify_channel")
def verify_channel_callback(call):
    user_id = call.from_user.id
    if check_channel(user_id):
        cursor.execute("INSERT OR REPLACE INTO users (user_id, joined_channel) VALUES (?,1)", (user_id,))
        conn.commit()
        get_wallet(user_id)
        bot.answer_callback_query(call.id, "✅ Verified!")
        text = f"⚡ Welcome to Pulse Profit!\n\n💰 Balance: 0 🟡⭐"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))
    else:
        bot.answer_callback_query(call.id, "❌ You haven't joined yet!", show_alert=True)

# ================= EARN STARS =================
@bot.callback_query_handler(func=lambda c: c.data == "earn")
def earn_callback(call):
    user_id = call.from_user.id
    
    cursor.execute("SELECT joined_channel FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user or user[0] != 1:
        verify_channel_callback(call)
        return
    
    cooldown = check_cooldown(user_id, "earn", COOLDOWN_TIME)
    if cooldown > 0:
        bot.answer_callback_query(call.id, f"⏳ Wait {cooldown}s", show_alert=True)
        return
    
    reward = random.randint(1, 3)
    cursor.execute("UPDATE users_wallet SET stars = stars + ?, total_earned = total_earned + ?, tasks_done = tasks_done + 1 WHERE user_id=?", 
                   (reward, reward, user_id))
    conn.commit()
    log_action(user_id, "earn")
    
    wallet = get_wallet(user_id)
    bot.answer_callback_query(call.id, f"✅ +{reward} 🟡⭐")
    bot.edit_message_text(f"✅ You earned {reward} 🟡⭐\n\n💰 New balance: {wallet[1]} 🟡⭐", 
                         call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ================= PROFILE =================
@bot.callback_query_handler(func=lambda c: c.data == "profile")
def profile_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    name = get_user_name(user_id)
    
    text = f"""
👤 PROFILE

User: {name}
Balance: {wallet[1]} 🟡⭐
Total Earned: {wallet[2]} 🟡⭐
Referrals: {wallet[3]}
Tasks Done: {wallet[5]}
Premium: {'✅' if wallet[4] else '❌'}
Daily Withdrawn: {wallet[6]}/{MAX_DAILY_WITHDRAW}
"""
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ================= LEADERBOARD =================
@bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def leaderboard_callback(call):
    placeholders = ','.join('?' * len(ADMIN_IDS))
    cursor.execute(f"SELECT user_id, stars FROM users_wallet WHERE user_id NOT IN ({placeholders}) ORDER BY stars DESC LIMIT 10", ADMIN_IDS)
    top = cursor.fetchall()
    
    text = "🏆 LEADERBOARD\n\n"
    if top:
        for i, (uid, stars) in enumerate(top, 1):
            name = get_user_name(uid)
            text += f"{i}. {name[:20]} - {stars} 🟡⭐\n"
    else:
        text += "No users yet.\n"
    
    cursor.execute(f"SELECT COUNT(*) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    total = cursor.fetchone()[0]
    cursor.execute(f"SELECT SUM(stars) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    total_stars = cursor.fetchone()[0] or 0
    
    text += f"\nTotal Users: {total}\nTotal Stars: {total_stars} 🟡⭐"
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(call.from_user.id))

# ================= REFERRAL =================
@bot.callback_query_handler(func=lambda c: c.data == "refer")
def refer_callback(call):
    user_id = call.from_user.id
    bot_name = bot.get_me().username
    link = f"https://t.me/{bot_name}?start={user_id}"
    
    text = f"""
📨 REFER & EARN

Your referrals: {get_wallet(user_id)[3]}

Earn 5 🟡⭐ per referral!

Your link:
`{link}`
"""
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ================= PREMIUM =================
@bot.callback_query_handler(func=lambda c: c.data == "premium")
def premium_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if wallet[4] == 1:
        text = "💎 PREMIUM ACTIVE\n\nYou have premium access!"
    else:
        text = "💎 PREMIUM\n\nGet premium to unlock:\n• Withdrawals\n• Admin requests\n• More rewards"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("💎 GET PREMIUM", url="https://t.me/MA5T3RBot"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================= BUY STARS =================
@bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
def buy_menu_callback(call):
    text = "🟡 BUY STARS\n\nChoose a package:"
    markup = InlineKeyboardMarkup()
    for stars, price in STAR_PACKAGES.items():
        markup.row(InlineKeyboardButton(f"{stars} Stars - {price} ⭐️", callback_data=f"buy_{stars}"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def buy_callback(call):
    stars = call.data.split("_")[1]
    price = STAR_PACKAGES[stars]
    
    prices = [LabeledPrice(label=f"{stars} Stars", amount=price)]
    bot.send_invoice(
        call.message.chat.id,
        title="Pulse Profit",
        description=f"Buy {stars} 🟡⭐ stars",
        invoice_payload=f"buy_{stars}",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="buy"
    )

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(q):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def payment_success(message):
    payload = message.successful_payment.invoice_payload
    stars = int(payload.split("_")[1])
    add_stars(message.from_user.id, stars)
    bot.send_message(message.chat.id, f"✅ Payment successful! +{stars} 🟡⭐", reply_markup=main_menu(message.from_user.id))

# ================= REDEEM CODE =================
@bot.callback_query_handler(func=lambda c: c.data == "redeem_menu")
def redeem_menu_callback(call):
    user_id = call.from_user.id
    text = "🎫 REDEEM CODE\n\nEnter your code:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type, action_time) VALUES (?,?,?)",
                   (user_id, "awaiting_code", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= WITHDRAWAL =================
@bot.callback_query_handler(func=lambda c: c.data == "withdraw_menu")
def withdraw_menu_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    text = f"""
💳 WITHDRAWAL

Balance: {wallet[1]} 🟡⭐
Daily: {wallet[6]}/{MAX_DAILY_WITHDRAW}

⭐ Stars Withdrawal (1:1)
Minimum: {MIN_WITHDRAW}
"""
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("⭐ WITHDRAW STARS", callback_data="withdraw_stars"))
    if wallet[4] == 1 or is_admin(user_id):
        markup.row(InlineKeyboardButton("💼 ADMIN REQUEST", callback_data="withdraw_admin"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_stars")
def withdraw_stars_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if wallet[1] < MIN_WITHDRAW:
        bot.answer_callback_query(call.id, f"❌ Need {MIN_WITHDRAW} 🟡⭐", show_alert=True)
        return
    
    cooldown = check_cooldown(user_id, "withdraw", WITHDRAWAL_COOLDOWN)
    if cooldown > 0:
        bot.answer_callback_query(call.id, f"⏳ Wait {cooldown}s", show_alert=True)
        return
    
    presets = [50, 100, 200, 500]
    text = f"⭐ Choose amount (balance: {wallet[1]} 🟡⭐):"
    markup = InlineKeyboardMarkup()
    row = []
    for amt in presets:
        if amt <= wallet[1]:
            row.append(InlineKeyboardButton(f"{amt}", callback_data=f"withdraw_amt_{amt}"))
            if len(row) == 2:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)
    markup.row(InlineKeyboardButton("✏️ CUSTOM", callback_data="withdraw_custom"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="withdraw_menu"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_amt_"))
def withdraw_amt_callback(call):
    amount = int(call.data.replace("withdraw_amt_", ""))
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if amount > wallet[1]:
        bot.answer_callback_query(call.id, "❌ Insufficient balance!", show_alert=True)
        return
    
    if not is_admin(user_id) and wallet[6] + amount > MAX_DAILY_WITHDRAW:
        bot.answer_callback_query(call.id, "❌ Daily limit exceeded!", show_alert=True)
        return
    
    log_action(user_id, "withdraw")
    cursor.execute("INSERT INTO withdraw_requests (user_id, amount, withdrawal_type) VALUES (?,?,'stars')", (user_id, amount))
    cursor.execute("UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    
    bot.answer_callback_query(call.id, f"✅ Requested {amount} ⭐️")
    bot.edit_message_text(f"✅ Withdrawal requested! {amount} ⭐️ will be sent soon.",
                         call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_custom")
def withdraw_custom_callback(call):
    user_id = call.from_user.id
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "awaiting_withdraw"))
    conn.commit()
    bot.edit_message_text("💰 Enter amount:", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_admin")
def withdraw_admin_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if not is_admin(user_id) and wallet[4] == 0:
        bot.answer_callback_query(call.id, "❌ Premium required!", show_alert=True)
        return
    
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "awaiting_withdraw"))
    conn.commit()
    bot.edit_message_text("💰 Enter amount for admin withdrawal:", call.message.chat.id, call.message.message_id)

# ================= BACK BUTTON =================
@bot.callback_query_handler(func=lambda c: c.data == "back")
def back_callback(call):
    bot.edit_message_text("⚡ Pulse Profit", call.message.chat.id, call.message.message_id, 
                         reply_markup=main_menu(call.from_user.id))

# ================= TASKS DISPLAY =================
@bot.callback_query_handler(func=lambda c: c.data == "show_tasks")
def show_tasks_callback(call):
    user_id = call.from_user.id
    
    cursor.execute("SELECT id, task_name, reward FROM tasks WHERE active=1")
    tasks = cursor.fetchall()
    
    if not tasks:
        bot.answer_callback_query(call.id, "No tasks available", show_alert=True)
        return
    
    text = "📋 AVAILABLE TASKS\n\n"
    markup = InlineKeyboardMarkup()
    for t in tasks:
        text += f"• {t[1]} - {t[2]}⭐\n"
        markup.row(InlineKeyboardButton(f"✓ {t[1][:20]}", callback_data=f"do_task_{t[0]}"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("do_task_"))
def do_task_callback(call):
    user_id = call.from_user.id
    task_id = int(call.data.replace("do_task_", ""))
    
    cursor.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id))
    if cursor.fetchone():
        bot.answer_callback_query(call.id, "You already did this task!", show_alert=True)
        return
    
    cursor.execute("SELECT task_type, task_data, reward FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    if not task:
        bot.answer_callback_query(call.id, "Task not found!", show_alert=True)
        return
    
    task_type, task_data, reward = task
    
    if task_type in ["join_channel", "join_group"]:
        try:
            chat_id = task_data.replace("https://t.me/", "").replace("@", "")
            if not chat_id.startswith("@"):
                chat_id = "@" + chat_id
            member = bot.get_chat_member(chat_id, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                cursor.execute("INSERT INTO user_tasks (user_id, task_id, verified) VALUES (?,?,1)", (user_id, task_id))
                add_stars(user_id, reward)
                conn.commit()
                bot.answer_callback_query(call.id, f"✅ +{reward}⭐", show_alert=True)
                wallet = get_wallet(user_id)
                bot.edit_message_text(f"✅ Task completed! +{reward}⭐\n\nNew balance: {wallet[1]}⭐", 
                                     call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))
            else:
                bot.answer_callback_query(call.id, "❌ You haven't joined yet!", show_alert=True)
        except:
            bot.answer_callback_query(call.id, "❌ Error verifying join", show_alert=True)
    else:
        cursor.execute("INSERT INTO user_tasks (user_id, task_id, verified) VALUES (?,?,0)", (user_id, task_id))
        conn.commit()
        bot.answer_callback_query(call.id, "Submitted for verification", show_alert=True)
        bot.edit_message_text("✅ Task submitted for admin verification", 
                             call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ================= ADMIN PANEL =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def admin_panel_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "❌ Access denied", show_alert=True)
        return
    
    placeholders = ','.join('?' * len(ADMIN_IDS))
    cursor.execute(f"SELECT COUNT(*) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE active=1")
    tasks = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM withdraw_requests WHERE status='pending'")
    pending = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_tasks WHERE verified=0")
    verify = cursor.fetchone()[0]
    
    text = f"""
👑 ADMIN PANEL

Users: {users}
Active Tasks: {tasks}
Pending Withdrawals: {pending}
Pending Verifications: {verify}

Choose option:
"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📋 TASKS", callback_data="admin_tasks"),
        InlineKeyboardButton("🎫 CODES", callback_data="admin_codes")
    )
    markup.row(
        InlineKeyboardButton("💳 WITHDRAWALS", callback_data="admin_withdrawals"),
        InlineKeyboardButton("🔍 VERIFY", callback_data="admin_verify")
    )
    markup.row(
        InlineKeyboardButton("📊 STATS", callback_data="admin_stats"),
        InlineKeyboardButton("💾 BACKUP", callback_data="admin_backup")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK", callback_data="back")
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================= ADMIN TASKS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_tasks")
def admin_tasks_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("SELECT id, task_name, reward, active FROM tasks ORDER BY id DESC LIMIT 10")
    tasks = cursor.fetchall()
    
    text = "📋 TASKS\n\n"
    if tasks:
        for t in tasks:
            status = "✅" if t[3] else "❌"
            text += f"{status} ID:{t[0]} - {t[1][:20]} - {t[2]}⭐\n"
    else:
        text += "No tasks yet.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("➕ ADD", callback_data="admin_add_task"),
        InlineKeyboardButton("❌ DELETE", callback_data="admin_del_task")
    )
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================= ADD TASK =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_add_task")
def admin_add_task_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    text = "➕ ADD TASK\n\nEnter task name:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "add_task_name"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= TASK TYPE CALLBACKS =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("task_type_"))
def task_type_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    type_map = {
        "channel": "join_channel",
        "group": "join_group",
        "link": "visit_link",
        "video": "watch_video"
    }
    task_type = type_map[call.data.replace("task_type_", "")]
    
    data = cursor.execute("SELECT session_data FROM admin_sessions WHERE admin_id=?", (user_id,)).fetchone()
    if not data:
        bot.answer_callback_query(call.id, "Session expired", show_alert=True)
        return
    
    task = json.loads(data[0])
    task["type"] = task_type
    cursor.execute("UPDATE admin_sessions SET session_data=? WHERE admin_id=?", (json.dumps(task), user_id))
    cursor.execute("UPDATE user_actions SET action_type=? WHERE user_id=?", ("add_task_data", user_id))
    conn.commit()
    
    bot.edit_message_text("🔗 Enter link/data:", call.message.chat.id, call.message.message_id)

# ================= DELETE TASK =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_del_task")
def admin_del_task_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    text = "❌ DELETE TASK\n\nEnter Task ID:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "del_task"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= ADMIN CODES =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_codes")
def admin_codes_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("SELECT id, code, amount, used FROM redeem_codes ORDER BY id DESC LIMIT 10")
    codes = cursor.fetchall()
    
    text = "🎫 REDEEM CODES\n\n"
    if codes:
        for c in codes:
            status = "✅" if c[3] else "🔄"
            text += f"{status} {c[1]} - {c[2]}⭐\n"
    else:
        text += "No codes yet.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("➕ CREATE", callback_data="admin_create_code"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================= CREATE CODE =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_create_code")
def admin_create_code_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    text = "➕ CREATE CODE\n\nEnter amount:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "create_code"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= ADMIN WITHDRAWALS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_withdrawals")
def admin_withdrawals_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("SELECT id, user_id, amount, request_time FROM withdraw_requests WHERE status='pending'")
    pending = cursor.fetchall()
    
    text = "💳 PENDING WITHDRAWALS\n\n"
    if pending:
        for p in pending:
            name = get_user_name(p[1])
            text += f"ID:{p[0]} - {name[:15]} - {p[2]}⭐ - {p[3][:16]}\n"
    else:
        text += "None\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("✅ APPROVE", callback_data="admin_approve_withdraw"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data == "admin_approve_withdraw")
def admin_approve_withdraw_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    text = "Enter withdrawal ID to approve:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "approve_withdraw"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= ADMIN VERIFY =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_verify")
def admin_verify_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("""
        SELECT ut.id, ut.user_id, t.task_name, t.reward 
        FROM user_tasks ut 
        JOIN tasks t ON ut.task_id=t.id 
        WHERE ut.verified=0
    """)
    pending = cursor.fetchall()
    
    text = "🔍 PENDING VERIFICATIONS\n\n"
    if pending:
        for p in pending:
            name = get_user_name(p[1])
            text += f"ID:{p[0]} - {name[:15]} - {p[2][:15]} - {p[3]}⭐\n"
    else:
        text += "None\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("✅ VERIFY", callback_data="admin_verify_action"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data == "admin_verify_action")
def admin_verify_action_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    text = "Enter verification ID:"
    cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "verify_id"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id)

# ================= ADMIN STATS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_stats")
def admin_stats_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    placeholders = ','.join('?' * len(ADMIN_IDS))
    
    cursor.execute(f"SELECT COUNT(*) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    users = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT SUM(stars) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    stars = cursor.fetchone()[0] or 0
    
    cursor.execute(f"SELECT AVG(stars) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    avg = int(cursor.fetchone()[0] or 0)
    
    cursor.execute("SELECT COUNT(*) FROM tasks WHERE active=1")
    tasks = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_tasks WHERE verified=1")
    completed = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM withdraw_requests WHERE status='approved'")
    approved = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM redeem_codes")
    codes = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM redeemed_codes")
    redeemed = cursor.fetchone()[0]
    
    text = f"""
📊 STATISTICS

Users: {users}
Total Stars: {stars} 🟡
Average Stars: {avg} 🟡
Active Tasks: {tasks}
Completed Tasks: {completed}
Approved Withdrawals: {approved}
Total Codes: {codes}
Redeemed Codes: {redeemed}
"""
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ================= ADMIN BACKUP =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_backup")
def admin_backup_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    if not GITHUB_TOKEN or not GITHUB_REPO:
        text = "❌ GitHub backup not configured"
    else:
        cursor.execute("SELECT backup_time, backup_type, status FROM backup_log ORDER BY backup_time DESC LIMIT 5")
        backups = cursor.fetchall()
        text = "💾 BACKUP SYSTEM\n\nRecent Backups:\n"
        if backups:
            for b in backups:
                text += f"{b[0][:16]} - {b[1]} - {b[2]}\n"
        else:
            text += "No backups yet\n"
    
    markup = InlineKeyboardMarkup()
    if GITHUB_TOKEN and GITHUB_REPO:
        markup.row(InlineKeyboardButton("💾 BACKUP NOW", callback_data="admin_backup_now"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_now")
def admin_backup_now_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    bot.answer_callback_query(call.id, "🔄 Creating backup...")
    success = backup_to_github("manual", f"Manual backup by admin {user_id}")
    if success:
        bot.send_message(call.message.chat.id, "✅ Backup completed!")
    else:
        bot.send_message(call.message.chat.id, "❌ Backup failed!")

# ================= HANDLE ALL TEXT MESSAGES =================
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    cursor.execute("SELECT action_type FROM user_actions WHERE user_id=?", (user_id,))
    action = cursor.fetchone()
    
    if not action:
        return
    
    action_type = action[0]
    cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
    conn.commit()
    
    # Handle redeem code
    if action_type == "awaiting_code":
        code = text.upper()
        cursor.execute("SELECT id, amount, used FROM redeem_codes WHERE code=?", (code,))
        code_data = cursor.fetchone()
        
        if not code_data:
            bot.send_message(message.chat.id, "❌ Invalid code!", reply_markup=main_menu(user_id))
            return
        
        code_id, amount, used = code_data
        if used:
            bot.send_message(message.chat.id, "❌ Code already used!", reply_markup=main_menu(user_id))
            return
        
        cursor.execute("SELECT id FROM redeemed_codes WHERE code_id=? AND user_id=?", (code_id, user_id))
        if cursor.fetchone():
            bot.send_message(message.chat.id, "❌ You already used this code!", reply_markup=main_menu(user_id))
            return
        
        add_stars(user_id, amount)
        cursor.execute("UPDATE redeem_codes SET used=1 WHERE id=?", (code_id,))
        cursor.execute("INSERT INTO redeemed_codes (code_id, user_id) VALUES (?,?)", (code_id, user_id))
        conn.commit()
        
        wallet = get_wallet(user_id)
        bot.send_message(message.chat.id, f"✅ Code redeemed! +{amount} 🟡⭐\n\nNew balance: {wallet[1]} 🟡⭐", 
                        reply_markup=main_menu(user_id))
    
    # Handle withdrawal amount
    elif action_type == "awaiting_withdraw":
        try:
            amount = int(text)
            if amount < MIN_WITHDRAW:
                bot.send_message(message.chat.id, f"❌ Minimum withdrawal is {MIN_WITHDRAW} 🟡⭐", 
                                reply_markup=main_menu(user_id))
                return
            
            wallet = get_wallet(user_id)
            if amount > wallet[1]:
                bot.send_message(message.chat.id, "❌ Insufficient balance!", reply_markup=main_menu(user_id))
                return
            
            cooldown = check_cooldown(user_id, "withdraw", WITHDRAWAL_COOLDOWN)
            if cooldown > 0:
                bot.send_message(message.chat.id, f"⏳ Wait {cooldown}s", reply_markup=main_menu(user_id))
                return
            
            log_action(user_id, "withdraw")
            cursor.execute("INSERT INTO withdraw_requests (user_id, amount, withdrawal_type) VALUES (?,?,'stars')", 
                          (user_id, amount))
            cursor.execute("UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id=?", (amount, user_id))
            conn.commit()
            
            bot.send_message(message.chat.id, f"✅ Withdrawal requested! {amount} ⭐️ will be sent soon.", 
                            reply_markup=main_menu(user_id))
        except:
            bot.send_message(message.chat.id, "❌ Invalid amount!", reply_markup=main_menu(user_id))
    
    # Handle admin task creation
    elif action_type == "add_task_name":
        cursor.execute("INSERT OR REPLACE INTO admin_sessions (admin_id, session_data) VALUES (?,?)", 
                      (user_id, json.dumps({"name": text})))
        conn.commit()
        
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📢 CHANNEL", callback_data="task_type_channel"),
            InlineKeyboardButton("👥 GROUP", callback_data="task_type_group")
        )
        markup.row(
            InlineKeyboardButton("🔗 LINK", callback_data="task_type_link"),
            InlineKeyboardButton("🎥 VIDEO", callback_data="task_type_video")
        )
        bot.send_message(message.chat.id, "Choose task type:", reply_markup=markup)
    
    elif action_type == "add_task_data":
        data = cursor.execute("SELECT session_data FROM admin_sessions WHERE admin_id=?", (user_id,)).fetchone()
        if not data:
            bot.send_message(message.chat.id, "Session expired", reply_markup=main_menu(user_id))
            return
        task = json.loads(data[0])
        task["data"] = text
        cursor.execute("UPDATE admin_sessions SET session_data=? WHERE admin_id=?", (json.dumps(task), user_id))
        cursor.execute("UPDATE user_actions SET action_type=? WHERE user_id=?", ("add_task_reward", user_id))
        conn.commit()
        bot.send_message(message.chat.id, "💰 Enter reward amount:")
    
    elif action_type == "add_task_reward":
        try:
            reward = int(text)
            data = cursor.execute("SELECT session_data FROM admin_sessions WHERE admin_id=?", (user_id,)).fetchone()
            if not data:
                bot.send_message(message.chat.id, "Session expired", reply_markup=main_menu(user_id))
                return
            task = json.loads(data[0])
            
            cursor.execute("INSERT INTO tasks (task_name, task_type, task_data, reward, created_by) VALUES (?,?,?,?,?)",
                          (task["name"], task["type"], task["data"], reward, user_id))
            cursor.execute("DELETE FROM admin_sessions WHERE admin_id=?", (user_id,))
            conn.commit()
            
            bot.send_message(message.chat.id, "✅ Task created!", reply_markup=main_menu(user_id))
            if GITHUB_TOKEN and GITHUB_REPO:
                threading.Thread(target=backup_to_github, args=("new_task", f"Task created: {task['name']}"), daemon=True).start()
        except:
            bot.send_message(message.chat.id, "❌ Invalid number", reply_markup=main_menu(user_id))
    
    # Handle task deletion
    elif action_type == "del_task":
        try:
            task_id = int(text)
            cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            cursor.execute("DELETE FROM user_tasks WHERE task_id=?", (task_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ Task {task_id} deleted!", reply_markup=main_menu(user_id))
        except:
            bot.send_message(message.chat.id, "❌ Invalid ID", reply_markup=main_menu(user_id))
    
    # Handle code creation
    elif action_type == "create_code":
        try:
            amount = int(text)
            chars = string.ascii_uppercase + string.digits
            code = ''.join(random.choices(chars, k=8))
            code = f"{code[:4]}-{code[4:]}"
            
            cursor.execute("INSERT INTO redeem_codes (code, amount, created_by) VALUES (?,?,?)", (code, amount, user_id))
            conn.commit()
            
            bot.send_message(message.chat.id, f"✅ Code created: `{code}`\nAmount: {amount}⭐", 
                            parse_mode="Markdown", reply_markup=main_menu(user_id))
            if GITHUB_TOKEN and GITHUB_REPO:
                threading.Thread(target=backup_to_github, args=("new_code", f"Code created for {amount}⭐"), daemon=True).start()
        except:
            bot.send_message(message.chat.id, "❌ Invalid amount", reply_markup=main_menu(user_id))
    
    # Handle verification
    elif action_type == "verify_id":
        try:
            verify_id = int(text)
            cursor.execute("SELECT user_id, task_id FROM user_tasks WHERE id=?", (verify_id,))
            task = cursor.fetchone()
            
            if not task:
                bot.send_message(message.chat.id, "❌ Verification ID not found", reply_markup=main_menu(user_id))
                return
            
            target_user, task_id = task
            cursor.execute("SELECT reward FROM tasks WHERE id=?", (task_id,))
            reward = cursor.fetchone()[0]
            
            cursor.execute("UPDATE user_tasks SET verified=1 WHERE id=?", (verify_id,))
            add_stars(target_user, reward)
            conn.commit()
            
            bot.send_message(message.chat.id, f"✅ Verified! User got {reward}⭐", reply_markup=main_menu(user_id))
            
            try:
                bot.send_message(target_user, f"✅ Your task has been verified! +{reward}⭐")
            except:
                pass
        except:
            bot.send_message(message.chat.id, "❌ Invalid ID", reply_markup=main_menu(user_id))
    
    # Handle withdrawal approval
    elif action_type == "approve_withdraw":
        try:
            req_id = int(text)
            cursor.execute("SELECT user_id, amount FROM withdraw_requests WHERE id=? AND status='pending'", (req_id,))
            req = cursor.fetchone()
            if not req:
                bot.send_message(message.chat.id, "❌ Request not found", reply_markup=main_menu(user_id))
                return
            
            target_user, amount = req
            cursor.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
            conn.commit()
            
            bot.send_message(message.chat.id, f"✅ Withdrawal {req_id} approved!", reply_markup=main_menu(user_id))
            try:
                bot.send_message(target_user, f"✅ Your withdrawal of {amount} ⭐️ has been approved!")
            except:
                pass
        except:
            bot.send_message(message.chat.id, "❌ Invalid ID", reply_markup=main_menu(user_id))

# ================= ADMIN DAILY BONUS =================
def daily_admin_bonus():
    while True:
        time.sleep(86400)
        reset_daily_withdrawals()
        for admin in ADMIN_IDS:
            cursor.execute("UPDATE users_wallet SET stars = stars + 100 WHERE user_id=?", (admin,))
        conn.commit()
        print("✅ Admin daily bonus added")

threading.Thread(target=daily_admin_bonus, daemon=True).start()

# ================= WEBHOOK SETUP =================
def setup_webhook():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url:
        webhook_url = f"{render_url}/{TOKEN}"
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        print(f"✅ Webhook set to: {webhook_url}")
        return True
    return False

# ================= MAIN =================
if __name__ == "__main__":
    print("=" * 50)
    print("⚡ PULSE PROFIT BOT ⚡")
    print("=" * 50)
    print(f"👑 Admins: {len(ADMIN_IDS)}")
    print(f"📢 Channel: {REQUIRED_CHANNEL}")
    print(f"💰 Earning System: Active")
    print(f"👥 Referral System: Active")
    print(f"💳 Withdrawal System: Active")
    print(f"⭐ Telegram Stars: Active")
    print(f"📋 Task System: Active")
    print(f"🎫 Redeem Code System: Active")
    print(f"👑 Admin Panel: Active")
    print(f"💾 GitHub Backup: {'Active' if GITHUB_TOKEN and GITHUB_REPO else 'Disabled'}")
    print("=" * 50)
    
    setup_webhook()
    
    if RENDER_EXTERNAL_URL:
        keep_alive.health_url = f"{RENDER_EXTERNAL_URL}/health"
        keep_alive.start()
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
