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

# ================= PREMIUM BOT LINK =================
PREMIUM_BOT_LINK = "https://t.me/MA5T3RBot"

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

# Create all tables
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
CREATE TABLE IF NOT EXISTS premium_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
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
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    expires_at TIMESTAMP,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1
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

def generate_code():
    """Generate a random 8-character code in format XXXX-XXXX"""
    chars = string.ascii_uppercase + string.digits
    # Exclude confusing characters
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    code = ''.join(random.choices(chars, k=8))
    return f"{code[:4]}-{code[4:]}"

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

# ================= PREMIUM WITH GUIDE =================
@bot.callback_query_handler(func=lambda c: c.data == "premium")
def premium_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if wallet[4] == 1:
        text = "💎 PREMIUM ACTIVE\n\nYou have premium access!"
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    else:
        cursor.execute("SELECT id FROM premium_requests WHERE user_id=? AND status='pending'", (user_id,))
        existing_request = cursor.fetchone()
        
        if existing_request:
            text = "⏳ Your premium request is pending admin approval."
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
        else:
            text = f"""
💎 PREMIUM MEMBERSHIP GUIDE

Follow these steps to get premium access:

━━━━━━━━━━━━━━━━━━━━━
📝 **STEP 1:** Click "PURCHASE PREMIUM" below
━━━━━━━━━━━━━━━━━━━━━
• You'll be redirected to @MA5T3RBot
• Purchase the premium package there

━━━━━━━━━━━━━━━━━━━━━
📝 **STEP 2:** After purchase, click "REQUEST APPROVAL"
━━━━━━━━━━━━━━━━━━━━━
• Your request will be sent to admins
• You'll be notified when approved

━━━━━━━━━━━━━━━━━━━━━
✅ Once approved, you'll get:
• Withdrawals enabled
• Admin withdrawal requests
• Higher earning potential
• Priority support
━━━━━━━━━━━━━━━━━━━━━
"""
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("💎 PURCHASE PREMIUM", url=PREMIUM_BOT_LINK)
            )
            markup.row(
                InlineKeyboardButton("📝 REQUEST APPROVAL", callback_data="request_premium")
            )
            markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= REQUEST PREMIUM =================
@bot.callback_query_handler(func=lambda c: c.data == "request_premium")
def request_premium_callback(call):
    user_id = call.from_user.id
    user_name = get_user_name(user_id)
    
    cursor.execute("SELECT id FROM premium_requests WHERE user_id=? AND status='pending'", (user_id,))
    if cursor.fetchone():
        bot.answer_callback_query(call.id, "You already have a pending request!", show_alert=True)
        return
    
    cursor.execute("INSERT INTO premium_requests (user_id) VALUES (?)", (user_id,))
    conn.commit()
    
    # Notify all admins
    for admin_id in ADMIN_IDS:
        try:
            admin_text = f"""
🔔 **NEW PREMIUM REQUEST** 🔔

━━━━━━━━━━━━━━━━━━━━━
👤 **User:** {user_name}
🆔 **ID:** `{user_id}`
━━━━━━━━━━━━━━━━━━━━━

✅ **To APPROVE:**
`/approve_premium {user_id}`

❌ **To REJECT:**
`/reject_premium {user_id}`
━━━━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(admin_id, admin_text, parse_mode="Markdown")
        except:
            pass
    
    bot.answer_callback_query(call.id, "✅ Request sent to admins!", show_alert=True)
    text = f"""
✅ **PREMIUM REQUEST SENT**

━━━━━━━━━━━━━━━━━━━━━
Your request has been sent to the admins for approval.

You will be notified once your request is processed.

⏱️ **Estimated response time:** 5-30 minutes
━━━━━━━━━━━━━━━━━━━━━
"""
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id), parse_mode="Markdown")

# ================= APPROVE PREMIUM COMMAND =================
@bot.message_handler(commands=['approve_premium'])
def approve_premium(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ Usage: /approve_premium [user_id]")
            return
        
        target_user = int(parts[1])
        
        # Check if request exists
        cursor.execute("SELECT id FROM premium_requests WHERE user_id=? AND status='pending'", (target_user,))
        request = cursor.fetchone()
        
        if not request:
            bot.reply_to(message, f"❌ No pending premium request found for user {target_user}")
            return
        
        # Update premium status
        cursor.execute("UPDATE users_wallet SET premium=1 WHERE user_id=?", (target_user,))
        cursor.execute("UPDATE premium_requests SET status='approved' WHERE user_id=? AND status='pending'", (target_user,))
        conn.commit()
        
        bot.reply_to(message, f"✅ Premium approved for user {target_user}!")
        
        # Notify user
        try:
            user_text = f"""
✅ **PREMIUM APPROVED!** ✅

━━━━━━━━━━━━━━━━━━━━━
Dear user,

Your premium request has been **APPROVED** by an admin!

━━━━━━━━━━━━━━━━━━━━━
**You now have access to:**
• Withdrawals enabled
• Admin withdrawal requests
• Higher earning potential
• Priority support
━━━━━━━━━━━━━━━━━━━━━

Thank you for being a premium member! 🎉
"""
            bot.send_message(target_user, user_text, parse_mode="Markdown")
        except:
            pass
        
        # Log the action
        if GITHUB_TOKEN and GITHUB_REPO:
            threading.Thread(target=backup_to_github, args=("premium_approved", f"User {target_user} approved by admin {admin_id}"), daemon=True).start()
            
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID format. Please provide a valid numeric ID.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ================= REJECT PREMIUM COMMAND =================
@bot.message_handler(commands=['reject_premium'])
def reject_premium(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ Usage: /reject_premium [user_id]")
            return
        
        target_user = int(parts[1])
        
        # Check if request exists
        cursor.execute("SELECT id FROM premium_requests WHERE user_id=? AND status='pending'", (target_user,))
        request = cursor.fetchone()
        
        if not request:
            bot.reply_to(message, f"❌ No pending premium request found for user {target_user}")
            return
        
        # Update request status
        cursor.execute("UPDATE premium_requests SET status='rejected' WHERE user_id=? AND status='pending'", (target_user,))
        conn.commit()
        
        bot.reply_to(message, f"❌ Premium rejected for user {target_user}!")
        
        # Notify user
        try:
            user_text = f"""
❌ **PREMIUM REQUEST REJECTED** ❌

━━━━━━━━━━━━━━━━━━━━━
Dear user,

Your premium request has been **REJECTED** by an admin.

━━━━━━━━━━━━━━━━━━━━━
**Possible reasons:**
• Purchase not verified
• Insufficient payment
• Technical issue

━━━━━━━━━━━━━━━━━━━━━
Please contact support if you believe this is an error.
"""
            bot.send_message(target_user, user_text, parse_mode="Markdown")
        except:
            pass
        
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID format. Please provide a valid numeric ID.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

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

⭐ Stars Withdrawal (1:1) - Automatic
Minimum: {MIN_WITHDRAW}

💼 Admin Withdrawal - Manual approval
"""
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("⭐ AUTO WITHDRAW", callback_data="withdraw_stars"))
    if wallet[4] == 1 or is_admin(user_id):
        markup.row(InlineKeyboardButton("💼 ADMIN WITHDRAW", callback_data="withdraw_admin_menu"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

# ===== AUTO WITHDRAW (Stars) =====
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
            row.append(InlineKeyboardButton(f"{amt}", callback_data=f"withdraw_auto_{amt}"))
            if len(row) == 2:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)
    markup.row(InlineKeyboardButton("✏️ CUSTOM", callback_data="withdraw_auto_custom"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="withdraw_menu"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_auto_"))
def withdraw_auto_amount_callback(call):
    if call.data == "withdraw_auto_custom":
        user_id = call.from_user.id
        cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "awaiting_auto_withdraw"))
        conn.commit()
        bot.edit_message_text("💰 Enter amount:", call.message.chat.id, call.message.message_id)
        return
    
    amount = int(call.data.replace("withdraw_auto_", ""))
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
    bot.edit_message_text(f"✅ Auto withdrawal requested! {amount} ⭐️ will be sent soon.",
                         call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ===== ADMIN WITHDRAW (Manual approval) =====
@bot.callback_query_handler(func=lambda c: c.data == "withdraw_admin_menu")
def withdraw_admin_menu_callback(call):
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if not is_admin(user_id) and wallet[4] == 0:
        bot.answer_callback_query(call.id, "❌ Premium required!", show_alert=True)
        return
    
    if wallet[1] < MIN_WITHDRAW:
        bot.answer_callback_query(call.id, f"❌ Need {MIN_WITHDRAW} 🟡⭐", show_alert=True)
        return
    
    presets = [50, 100, 200, 500]
    text = f"💼 Choose amount for admin approval (balance: {wallet[1]} 🟡⭐):"
    markup = InlineKeyboardMarkup()
    row = []
    for amt in presets:
        if amt <= wallet[1]:
            row.append(InlineKeyboardButton(f"{amt}", callback_data=f"withdraw_admin_{amt}"))
            if len(row) == 2:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)
    markup.row(InlineKeyboardButton("✏️ CUSTOM", callback_data="withdraw_admin_custom"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="withdraw_menu"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_admin_"))
def withdraw_admin_amount_callback(call):
    if call.data == "withdraw_admin_custom":
        user_id = call.from_user.id
        cursor.execute("INSERT OR REPLACE INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "awaiting_admin_withdraw"))
        conn.commit()
        bot.edit_message_text("💰 Enter amount for admin approval:", call.message.chat.id, call.message.message_id)
        return
    
    amount = int(call.data.replace("withdraw_admin_", ""))
    user_id = call.from_user.id
    wallet = get_wallet(user_id)
    
    if amount > wallet[1]:
        bot.answer_callback_query(call.id, "❌ Insufficient balance!", show_alert=True)
        return
    
    if not is_admin(user_id) and wallet[6] + amount > MAX_DAILY_WITHDRAW:
        bot.answer_callback_query(call.id, "❌ Daily limit exceeded!", show_alert=True)
        return
    
    cursor.execute("INSERT INTO withdraw_requests (user_id, amount, withdrawal_type) VALUES (?,?,'admin')", (user_id, amount))
    cursor.execute("UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    
    user_name = get_user_name(user_id)
    for admin_id in ADMIN_IDS:
        try:
            admin_text = f"""
🔔 NEW ADMIN WITHDRAWAL REQUEST

👤 User: {user_name}
🆔 ID: `{user_id}`
💰 Amount: {amount} 🟡⭐

Use:
/approve_withdraw {user_id} {amount}
/reject_withdraw {user_id} {amount}
"""
            bot.send_message(admin_id, admin_text, parse_mode="Markdown")
        except:
            pass
    
    bot.answer_callback_query(call.id, f"✅ Requested {amount} ⭐️ for admin approval")
    bot.edit_message_text(f"✅ Admin withdrawal requested! {amount} ⭐️ is pending admin approval.",
                         call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))

# ================= APPROVE WITHDRAWAL COMMAND =================
@bot.message_handler(commands=['approve_withdraw'])
def approve_withdraw(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 3:
            bot.reply_to(message, "❌ Usage: /approve_withdraw [user_id] [amount]")
            return
        
        target_user = int(parts[1])
        amount = int(parts[2])
        
        cursor.execute("""
            SELECT id FROM withdraw_requests 
            WHERE user_id=? AND amount=? AND status='pending' AND withdrawal_type='admin'
            ORDER BY request_time DESC LIMIT 1
        """, (target_user, amount))
        req = cursor.fetchone()
        
        if not req:
            bot.reply_to(message, "❌ No pending request found!")
            return
        
        req_id = req[0]
        
        cursor.execute("UPDATE withdraw_requests SET status='approved' WHERE id=?", (req_id,))
        cursor.execute("UPDATE users_wallet SET stars = stars - ? WHERE user_id=?", (amount, target_user))
        conn.commit()
        
        bot.reply_to(message, f"✅ Withdrawal approved for user {target_user} (Amount: {amount}⭐)")
        
        try:
            bot.send_message(target_user, f"✅ Your admin withdrawal of {amount}⭐ has been approved!")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or amount format.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ================= REJECT WITHDRAWAL COMMAND =================
@bot.message_handler(commands=['reject_withdraw'])
def reject_withdraw(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        parts = message.text.split()
        if len(parts) < 3:
            bot.reply_to(message, "❌ Usage: /reject_withdraw [user_id] [amount]")
            return
        
        target_user = int(parts[1])
        amount = int(parts[2])
        
        cursor.execute("""
            UPDATE withdraw_requests SET status='rejected' 
            WHERE user_id=? AND amount=? AND status='pending' AND withdrawal_type='admin'
        """, (target_user, amount))
        
        if cursor.rowcount == 0:
            bot.reply_to(message, "❌ No pending request found!")
            return
        
        # Refund daily withdrawal limit
        cursor.execute("UPDATE users_wallet SET daily_withdrawn = daily_withdrawn - ? WHERE user_id=?", (amount, target_user))
        conn.commit()
        
        bot.reply_to(message, f"❌ Withdrawal rejected for user {target_user}")
        
        try:
            bot.send_message(target_user, f"❌ Your admin withdrawal of {amount}⭐ has been rejected.")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID or amount format.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

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
        text = "📋 No tasks available at the moment."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=main_menu(user_id))
        return
    
    text = "📋 **AVAILABLE TASKS**\n\nClick a task to complete it:\n\n"
    markup = InlineKeyboardMarkup()
    
    for task in tasks:
        task_id, task_name, reward = task
        markup.row(InlineKeyboardButton(f"✅ {task_name} - {reward}⭐", callback_data=f"do_task_{task_id}"))
    
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="back"))
    
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("do_task_"))
def do_task_callback(call):
    user_id = call.from_user.id
    task_id = int(call.data.replace("do_task_", ""))
    
    # Check if user already completed this task
    cursor.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id))
    if cursor.fetchone():
        bot.answer_callback_query(call.id, "You already did this task!", show_alert=True)
        return
    
    # Get task details
    cursor.execute("SELECT task_type, task_data, reward, task_name FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    if not task:
        bot.answer_callback_query(call.id, "Task not found!", show_alert=True)
        return
    
    task_type, task_data, reward, task_name = task
    
    if task_type in ["join_channel", "join_group"]:
        # Auto-verify channel/group join
        try:
            # Extract chat ID from task_data
            chat_id = task_data.replace("https://t.me/", "").replace("@", "")
            if not chat_id.startswith("@"):
                chat_id = "@" + chat_id
            
            # Check membership
            member = bot.get_chat_member(chat_id, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                # Complete task
                cursor.execute("INSERT INTO user_tasks (user_id, task_id, verified) VALUES (?,?,1)", (user_id, task_id))
                add_stars(user_id, reward)
                conn.commit()
                
                bot.answer_callback_query(call.id, f"✅ +{reward}⭐ Task completed!", show_alert=True)
                
                # Update message
                wallet = get_wallet(user_id)
                text = f"""
✅ **TASK COMPLETED!**

━━━━━━━━━━━━━━━━━━━━━
📋 **Task:** {task_name}
💰 **Reward:** +{reward}⭐

━━━━━━━━━━━━━━━━━━━━━
📊 **New Balance:** {wallet[1]}⭐
━━━━━━━━━━━━━━━━━━━━━
"""
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                                     reply_markup=main_menu(user_id), parse_mode="Markdown")
                
                # Backup on task completion
                if GITHUB_TOKEN and GITHUB_REPO:
                    threading.Thread(target=backup_to_github, args=("task_complete", f"User {user_id} completed task {task_id}"), daemon=True).start()
            else:
                bot.answer_callback_query(call.id, "❌ You haven't joined yet! Please join first.", show_alert=True)
        except Exception as e:
            print(f"Error verifying join: {e}")
            bot.answer_callback_query(call.id, "❌ Error verifying. Please make sure you've joined and try again.", show_alert=True)
    else:
        # Manual verification needed (visit_link, watch_video)
        cursor.execute("INSERT INTO user_tasks (user_id, task_id, verified) VALUES (?,?,0)", (user_id, task_id))
        conn.commit()
        
        # Notify admins
        user_name = get_user_name(user_id)
        for admin_id in ADMIN_IDS:
            try:
                admin_text = f"""
🔔 **TASK VERIFICATION NEEDED** 🔔

━━━━━━━━━━━━━━━━━━━━━
👤 **User:** {user_name}
🆔 **ID:** `{user_id}`
📋 **Task:** {task_name}
💰 **Reward:** {reward}⭐
━━━━━━━━━━━━━━━━━━━━━

✅ To verify, use:
`/verify_task {user_id} {task_name}`
━━━━━━━━━━━━━━━━━━━━━
"""
                bot.send_message(admin_id, admin_text, parse_mode="Markdown")
            except:
                pass
        
        bot.answer_callback_query(call.id, "✅ Task submitted for verification!", show_alert=True)
        
        text = f"""
✅ **TASK SUBMITTED FOR VERIFICATION**

━━━━━━━━━━━━━━━━━━━━━
📋 **Task:** {task_name}
💰 **Reward:** {reward}⭐ (pending)

━━━━━━━━━━━━━━━━━━━━━
⏳ An admin will verify your completion soon.
You'll be notified when it's approved.
━━━━━━━━━━━━━━━━━━━━━
"""
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, 
                             reply_markup=main_menu(user_id), parse_mode="Markdown")

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
    pending_withdrawals = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM premium_requests WHERE status='pending'")
    pending_premium = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM user_tasks WHERE verified=0")
    verify = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM redeem_codes WHERE active=1")
    codes = cursor.fetchone()[0]
    
    text = f"""
👑 **ADMIN PANEL**

━━━━━━━━━━━━━━━━━━━━━
📊 **STATISTICS**
━━━━━━━━━━━━━━━━━━━━━
👥 Users: {users}
📋 Active Tasks: {tasks}
⏳ Pending Withdrawals: {pending_withdrawals}
👑 Pending Premium: {pending_premium}
🔍 Pending Verifications: {verify}
🎫 Active Codes: {codes}
━━━━━━━━━━━━━━━━━━━━━

🛠️ **Choose an option:**
"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📋 MANAGE TASKS", callback_data="admin_tasks"),
        InlineKeyboardButton("🎫 MANAGE CODES", callback_data="admin_codes")
    )
    markup.row(
        InlineKeyboardButton("💳 WITHDRAWALS", callback_data="admin_withdrawals"),
        InlineKeyboardButton("👑 PREMIUM", callback_data="admin_premium")
    )
    markup.row(
        InlineKeyboardButton("🔍 VERIFY TASKS", callback_data="admin_verify"),
        InlineKeyboardButton("📊 STATS", callback_data="admin_stats")
    )
    markup.row(
        InlineKeyboardButton("💾 BACKUP", callback_data="admin_backup"),
        InlineKeyboardButton("🔙 BACK", callback_data="back")
    )
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= ADMIN TASKS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_tasks")
def admin_tasks_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("SELECT id, task_name, task_type, reward, active FROM tasks ORDER BY id DESC LIMIT 10")
    tasks = cursor.fetchall()
    
    text = "📋 **TASK MANAGEMENT**\n\n"
    if tasks:
        for t in tasks:
            status = "✅" if t[4] else "❌"
            text += f"{status} ID: `{t[0]}` - **{t[1]}**\n"
            text += f"   Type: {t[2]} | Reward: {t[3]}⭐\n\n"
    else:
        text += "No tasks yet.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("➕ CREATE TASK", callback_data="admin_add_task"),
        InlineKeyboardButton("❌ DELETE TASK", callback_data="admin_del_task")
    )
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= ADD TASK =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_add_task")
def admin_add_task_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    # Clear any existing sessions
    cursor.execute("DELETE FROM admin_sessions WHERE admin_id=?", (user_id,))
    cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
    conn.commit()
    
    text = "➕ **CREATE NEW TASK**\n\nStep 1/4: Enter task name:"
    cursor.execute("INSERT INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "add_task_name"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

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
    
    # Get existing session data
    data = cursor.execute("SELECT session_data FROM admin_sessions WHERE admin_id=?", (user_id,)).fetchone()
    if not data:
        bot.answer_callback_query(call.id, "Session expired. Please start over.", show_alert=True)
        return
    
    # Update session with task type
    task = json.loads(data[0])
    task["type"] = task_type
    cursor.execute("UPDATE admin_sessions SET session_data=?, updated_at=? WHERE admin_id=?", 
                  (json.dumps(task), datetime.now(), user_id))
    
    # Update user action to next step
    cursor.execute("UPDATE user_actions SET action_type=?, action_time=? WHERE user_id=?", 
                  ("add_task_data", datetime.now(), user_id))
    conn.commit()
    
    bot.edit_message_text("🔗 **Step 3/4:** Enter the link or channel username:\n\nExample: @channel or https://t.me/channel", 
                         call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ================= DELETE TASK =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_del_task")
def admin_del_task_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    # Clear any existing actions
    cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
    conn.commit()
    
    text = "❌ **DELETE TASK**\n\nEnter the Task ID to delete:"
    cursor.execute("INSERT INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "del_task"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ================= ADMIN CODES =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_codes")
def admin_codes_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("SELECT id, code, amount, max_uses, used_count, expires_at, active FROM redeem_codes ORDER BY id DESC LIMIT 10")
    codes = cursor.fetchall()
    
    text = "🎫 **REDEEM CODE MANAGEMENT**\n\n"
    if codes:
        for c in codes:
            status = "✅" if c[6] else "❌"
            expires = c[5][:10] if c[5] else "Never"
            text += f"{status} `{c[1]}`\n"
            text += f"   Amount: {c[2]}⭐ | Used: {c[4]}/{c[3]} | Exp: {expires}\n\n"
    else:
        text += "No codes yet.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("➕ CREATE CODE", callback_data="admin_create_code"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= CREATE CODE =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_create_code")
def admin_create_code_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    # Clear any existing sessions
    cursor.execute("DELETE FROM admin_sessions WHERE admin_id=?", (user_id,))
    cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
    conn.commit()
    
    text = "➕ **CREATE REDEEM CODE**\n\nStep 1/3: Enter the star amount:"
    cursor.execute("INSERT INTO user_actions (user_id, action_type) VALUES (?,?)", (user_id, "create_code_amount"))
    conn.commit()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# ================= ADMIN WITHDRAWALS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_withdrawals")
def admin_withdrawals_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("""
        SELECT id, user_id, amount, request_time 
        FROM withdraw_requests 
        WHERE status='pending' AND withdrawal_type='admin'
        ORDER BY request_time ASC
    """)
    pending = cursor.fetchall()
    
    text = "💳 **PENDING ADMIN WITHDRAWALS**\n\n"
    if pending:
        for p in pending:
            name = get_user_name(p[1])
            text += f"• **{name}** (ID: `{p[1]}`)\n"
            text += f"  Amount: {p[2]}⭐ | Time: {p[3][:16]}\n"
            text += f"  Approve: `/approve_withdraw {p[1]} {p[2]}`\n"
            text += f"  Reject: `/reject_withdraw {p[1]} {p[2]}`\n\n"
    else:
        text += "No pending withdrawals.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= ADMIN PREMIUM REQUESTS =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_premium")
def admin_premium_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    cursor.execute("""
        SELECT pr.id, pr.user_id, u.first_name, pr.request_time 
        FROM premium_requests pr
        LEFT JOIN users u ON pr.user_id = u.user_id
        WHERE pr.status='pending'
        ORDER BY pr.request_time ASC
    """)
    pending = cursor.fetchall()
    
    text = "👑 **PENDING PREMIUM REQUESTS**\n\n"
    if pending:
        for req in pending:
            name = req[2] or f"User {req[1]}"
            text += f"• **{name}** (ID: `{req[1]}`)\n"
            text += f"  Time: {req[3][:16]}\n"
            text += f"  Approve: `/approve_premium {req[1]}`\n"
            text += f"  Reject: `/reject_premium {req[1]}`\n\n"
    else:
        text += "No pending requests.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

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
        ORDER BY ut.completed_at ASC
    """)
    pending = cursor.fetchall()
    
    text = "🔍 **PENDING TASK VERIFICATIONS**\n\n"
    if pending:
        for p in pending:
            name = get_user_name(p[1])
            text += f"• **{name}** (ID: `{p[1]}`)\n"
            text += f"  Task: {p[2][:30]} | Reward: {p[3]}⭐\n"
            text += f"  Verify: `/verify_task {p[1]} {p[2]}`\n\n"
    else:
        text += "No pending verifications.\n"
    
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

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
    
    cursor.execute("SELECT COUNT(*) FROM premium_requests WHERE status='pending'")
    premium_pending = cursor.fetchone()[0]
    
    text = f"""
📊 **BOT STATISTICS**

━━━━━━━━━━━━━━━━━━━━━
👥 **Users:** {users}
💰 **Total Stars:** {stars} 🟡
📊 **Average Stars:** {avg} 🟡
━━━━━━━━━━━━━━━━━━━━━
📋 **Active Tasks:** {tasks}
✅ **Completed Tasks:** {completed}
━━━━━━━━━━━━━━━━━━━━━
💳 **Approved Withdrawals:** {approved}
👑 **Pending Premium:** {premium_pending}
━━━━━━━━━━━━━━━━━━━━━
🎫 **Total Codes:** {codes}
🔄 **Redeemed Codes:** {redeemed}
━━━━━━━━━━━━━━━━━━━━━
"""
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= ADMIN BACKUP =================
@bot.callback_query_handler(func=lambda c: c.data == "admin_backup")
def admin_backup_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    if not GITHUB_TOKEN or not GITHUB_REPO:
        text = "❌ GitHub backup is not configured.\n\nSet GITHUB_TOKEN and GITHUB_REPO environment variables to enable backups."
    else:
        cursor.execute("SELECT backup_time, backup_type, status FROM backup_log ORDER BY backup_time DESC LIMIT 5")
        backups = cursor.fetchall()
        text = "💾 **BACKUP SYSTEM**\n\n"
        if backups:
            text += "**Recent Backups:**\n"
            for b in backups:
                status_icon = "✅" if b[2] == "success" else "❌"
                text += f"{status_icon} {b[0][:16]} - {b[1]}\n"
        else:
            text += "No backups yet.\n"
    
    markup = InlineKeyboardMarkup()
    if GITHUB_TOKEN and GITHUB_REPO:
        markup.row(InlineKeyboardButton("💾 BACKUP NOW", callback_data="admin_backup_now"))
    markup.row(InlineKeyboardButton("🔙 BACK", callback_data="admin_panel"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "admin_backup_now")
def admin_backup_now_callback(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        return
    
    bot.answer_callback_query(call.id, "🔄 Creating backup...")
    success = backup_to_github("manual", f"Manual backup by admin {user_id}")
    if success:
        bot.send_message(call.message.chat.id, "✅ Backup completed successfully!")
    else:
        bot.send_message(call.message.chat.id, "❌ Backup failed! Check GitHub configuration.")

# ================= VERIFY TASK COMMAND =================
@bot.message_handler(commands=['verify_task'])
def verify_task_command(message):
    admin_id = message.from_user.id
    if not is_admin(admin_id):
        bot.reply_to(message, "❌ You are not authorized to use this command.")
        return
    
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            bot.reply_to(message, "❌ Usage: /verify_task [user_id] [task_name]")
            return
        
        target_user = int(parts[1])
        task_name = parts[2]
        
        # Find the pending task
        cursor.execute("""
            SELECT ut.id, t.reward, t.id FROM user_tasks ut
            JOIN tasks t ON ut.task_id = t.id
            WHERE ut.user_id=? AND t.task_name LIKE ? AND ut.verified=0
            ORDER BY ut.completed_at DESC LIMIT 1
        """, (target_user, f"%{task_name}%"))
        task = cursor.fetchone()
        
        if not task:
            bot.reply_to(message, f"❌ No pending task found for user {target_user} with name '{task_name}'")
            return
        
        task_id, reward, t_id = task
        
        cursor.execute("UPDATE user_tasks SET verified=1 WHERE id=?", (task_id,))
        add_stars(target_user, reward)
        conn.commit()
        
        bot.reply_to(message, f"✅ Task verified! User {target_user} got {reward}⭐")
        
        try:
            bot.send_message(target_user, f"✅ Your task '{task_name}' has been verified! +{reward}⭐")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ Invalid user ID format.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

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
    print(f"Processing action: {action_type} for user {user_id} with text: {text}")
    
    # Don't delete yet - we'll delete after processing
    # cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
    # conn.commit()
    
    # Handle redeem code
    if action_type == "awaiting_code":
        cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
        conn.commit()
        
        code = text.upper()
        cursor.execute("SELECT id, amount, max_uses, used_count, expires_at, active FROM redeem_codes WHERE code=?", (code,))
        code_data = cursor.fetchone()
        
        if not code_data:
            bot.send_message(message.chat.id, "❌ Invalid code!", reply_markup=main_menu(user_id))
            return
        
        code_id, amount, max_uses, used_count, expires_at, active = code_data
        
        if not active:
            bot.send_message(message.chat.id, "❌ Code is deactivated!", reply_markup=main_menu(user_id))
            return
        
        if expires_at:
            expires = datetime.fromisoformat(expires_at)
            if datetime.now() > expires:
                bot.send_message(message.chat.id, "❌ Code has expired!", reply_markup=main_menu(user_id))
                return
        
        if used_count >= max_uses:
            bot.send_message(message.chat.id, "❌ Code has reached maximum uses!", reply_markup=main_menu(user_id))
            return
        
        cursor.execute("SELECT id FROM redeemed_codes WHERE code_id=? AND user_id=?", (code_id, user_id))
        if cursor.fetchone():
            bot.send_message(message.chat.id, "❌ You already used this code!", reply_markup=main_menu(user_id))
            return
        
        add_stars(user_id, amount)
        cursor.execute("UPDATE redeem_codes SET used_count = used_count + 1 WHERE id=?", (code_id,))
        cursor.execute("INSERT INTO redeemed_codes (code_id, user_id) VALUES (?,?)", (code_id, user_id))
        conn.commit()
        
        wallet = get_wallet(user_id)
        bot.send_message(message.chat.id, f"✅ Code redeemed! +{amount} 🟡⭐\n\nNew balance: {wallet[1]} 🟡⭐", 
                        reply_markup=main_menu(user_id))
    
    # Handle auto withdrawal amount
    elif action_type == "awaiting_auto_withdraw":
        cursor.execute("DELETE FROM user_actions WHERE user_id=?", (user_id,))
        conn.commit()
        
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
                bot.send_message(message.chat.id, f"
