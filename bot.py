import os
import random
import sqlite3
import requests
import threading
import time
import base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

# ================= ENV =================
TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # username/repo
GITHUB_FILE_PATH = "promo.db"

# Initialize bot and app
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ================= ADMINS =================
ADMIN_IDS = [7475473197, 7713987088]  # Replace with your real admin IDs

# ================= COOLDOWN SETTINGS =================
COOLDOWN_TIME = 60  # seconds between earning attempts
WITHDRAWAL_COOLDOWN = 3600  # 1 hour between withdrawal requests
MIN_WITHDRAW = 50
MAX_DAILY_WITHDRAW = 500  # Maximum withdrawal per user per day

# ================= TELEGRAM STARS PRICES =================
STAR_PACKAGES = {
    "10": 10,    # 10 Stars for 10 XTR
    "50": 45,    # 50 Stars for 45 XTR (10% discount)
    "100": 85,   # 100 Stars for 85 XTR (15% discount)
    "500": 400,  # 500 Stars for 400 XTR (20% discount)
    "1000": 750  # 1000 Stars for 750 XTR (25% discount)
}

# ================= DATABASE =================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

# Create tables
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
    status TEXT DEFAULT 'pending',
    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_time TIMESTAMP DEFAULT NULL
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
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    telegram_payment_charge_id TEXT,
    stars_purchased INTEGER,
    amount_paid INTEGER,
    payment_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        """Start keep-alive service"""
        self.is_running = True
        
        def ping_loop():
            while self.is_running:
                try:
                    self.ping_count += 1
                    if self.health_url:
                        response = requests.get(self.health_url, timeout=15)
                        if response.status_code == 200:
                            print(f"✅ Keep-alive ping #{self.ping_count}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                    time.sleep(240)  # Ping every 4 minutes
                except Exception as e:
                    print(f"❌ Keep-alive error: {e}")
                    time.sleep(60)
        
        thread = threading.Thread(target=ping_loop, daemon=True)
        thread.start()
        print("🔄 Keep-alive service started")
        
    def stop(self):
        self.is_running = False
        print("🛑 Keep-alive service stopped")

# ================= FLASK HEALTH ENDPOINTS =================

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'service': 'Gold Ultimate Bot',
        'timestamp': time.time()
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': time.time(),
        'pings': keep_alive.ping_count if 'keep_alive' in globals() else 0
    }), 200

# ================= HELPER FUNCTIONS =================

def get_wallet(user_id):
    """Get or create user wallet"""
    cursor.execute("SELECT * FROM users_wallet WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.execute("INSERT INTO users_wallet (user_id) VALUES (?)", (user_id,))
        conn.commit()
        return get_wallet(user_id)
    return user

def add_stars(user_id, amount):
    """Add stars to user wallet"""
    cursor.execute("""
        UPDATE users_wallet
        SET stars = stars + ?, total_earned = total_earned + ?
        WHERE user_id=?
    """, (amount, amount, user_id))
    conn.commit()

def check_cooldown(user_id, action, cooldown_seconds):
    """Check if user is on cooldown for specific action"""
    cursor.execute("""
        SELECT action_time FROM user_actions 
        WHERE user_id = ? AND action_type = ?
        ORDER BY action_time DESC LIMIT 1
    """, (user_id, action))
    
    last_action = cursor.fetchone()
    if last_action:
        last_time = datetime.strptime(last_action[0], '%Y-%m-%d %H:%M:%S')
        time_diff = (datetime.now() - last_time).total_seconds()
        if time_diff < cooldown_seconds:
            return int(cooldown_seconds - time_diff)
    return 0

def log_action(user_id, action_type):
    """Log user action for cooldown tracking"""
    cursor.execute("""
        INSERT INTO user_actions (user_id, action_type, action_time)
        VALUES (?, ?, ?)
    """, (user_id, action_type, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()

def reset_daily_withdrawals():
    """Reset daily withdrawal limits for all users"""
    cursor.execute("UPDATE users_wallet SET daily_withdrawn = 0")
    conn.commit()
    print("✅ Daily withdrawal limits reset")

# ================= AUTO WITHDRAWAL PROCESSOR =================

def process_withdrawals():
    """Automatically process pending withdrawals"""
    while True:
        time.sleep(300)  # Check every 5 minutes
        
        cursor.execute("""
            SELECT id, user_id, amount FROM withdraw_requests 
            WHERE status = 'pending' 
            ORDER BY request_time ASC
        """)
        pending = cursor.fetchall()
        
        for req_id, user_id, amount in pending:
            user = get_wallet(user_id)
            
            if user[1] >= amount:
                cursor.execute("""
                    UPDATE users_wallet 
                    SET stars = stars - ? 
                    WHERE user_id = ?
                """, (amount, user_id))
                
                cursor.execute("""
                    UPDATE withdraw_requests 
                    SET status = 'approved', processed_time = ? 
                    WHERE id = ?
                """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
                
                conn.commit()
                
                try:
                    bot.send_message(
                        user_id, 
                        f"✅ Your withdrawal of {amount} 🟡⭐ has been automatically approved and processed!"
                    )
                except:
                    pass
            else:
                cursor.execute("""
                    UPDATE withdraw_requests 
                    SET status = 'rejected', processed_time = ? 
                    WHERE id = ?
                """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
                conn.commit()
                
                try:
                    bot.send_message(
                        user_id, 
                        f"❌ Your withdrawal request for {amount} 🟡⭐ was rejected due to insufficient balance."
                    )
                except:
                    pass

# Start withdrawal processor thread
threading.Thread(target=process_withdrawals, daemon=True).start()

# ================= MAIN MENU =================

def main_menu():
    """Create main menu keyboard"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💼✨ Earn Stars", callback_data="earn"),
        InlineKeyboardButton("📨🔥 Refer & Earn", callback_data="refer")
    )
    markup.row(
        InlineKeyboardButton("👤🌈 Profile", callback_data="profile"),
        InlineKeyboardButton("🏆🎖 Leaderboard", callback_data="leaderboard")
    )
    markup.row(
        InlineKeyboardButton("💎🚀 Premium", callback_data="premium"),
        InlineKeyboardButton("🟡💰 Buy Stars", callback_data="buy_menu")
    )
    markup.row(
        InlineKeyboardButton("💳🏦 Withdrawal", callback_data="withdraw")
    )
    return markup

# ================= START COMMAND =================

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    get_wallet(user_id)

    args = message.text.split()

    if len(args) > 1:
        try:
            referrer_id = int(args[1])
            if referrer_id != user_id:
                cursor.execute("SELECT * FROM referrals WHERE referred_id=?", (user_id,))
                already = cursor.fetchone()

                if not already:
                    cooldown = check_cooldown(referrer_id, "refer", COOLDOWN_TIME)
                    if cooldown == 0:
                        cursor.execute("INSERT INTO referrals VALUES (?,?)", (referrer_id, user_id))
                        cursor.execute("UPDATE users_wallet SET referrals = referrals + 1 WHERE user_id=?", (referrer_id,))
                        add_stars(referrer_id, 5)
                        log_action(referrer_id, "refer")
                        conn.commit()
                        bot.send_message(referrer_id, "🎉 You earned 5 🟡⭐ from referral!")
                    else:
                        bot.send_message(referrer_id, f"⏳ Please wait {cooldown} seconds before next referral!")
        except:
            pass

    bot.send_message(
        user_id, 
        "🔥 Welcome to Gold Ultimate Bot!\n\nEarn stars, refer friends, and withdraw your earnings!",
        reply_markup=main_menu()
    )

# ================= EARN STARS =================

@bot.callback_query_handler(func=lambda c: c.data == "earn")
def earn(call):
    user_id = call.from_user.id
    
    cooldown = check_cooldown(user_id, "earn", COOLDOWN_TIME)
    
    if cooldown > 0:
        bot.answer_callback_query(
            call.id, 
            f"⏳ Please wait {cooldown} seconds before earning again!",
            show_alert=True
        )
        return
    
    reward = random.randint(1, 3)
    
    cursor.execute("""
        UPDATE users_wallet 
        SET stars = stars + ?, total_earned = total_earned + ?, tasks_done = tasks_done + 1
        WHERE user_id=?
    """, (reward, reward, user_id))
    conn.commit()
    
    log_action(user_id, "earn")
    
    user = get_wallet(user_id)
    bot.edit_message_text(
        f"✅ You earned {reward} 🟡⭐!\n\n💰 New balance: {user[1]} 🟡⭐",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu()
    )

# ================= PROFILE =================

@bot.callback_query_handler(func=lambda c: c.data == "profile")
def profile(call):
    user = get_wallet(call.from_user.id)

    text = f"""
🌈👤 **PROFILE PANEL**

👥 Referrals: {user[3]}
🎯 Tasks Done: {user[5]}

💰 Total Earned: {user[2]} 🟡⭐
⭐ Current Balance: {user[1]} 🟡⭐

💎 Premium: {"✅ ACTIVE" if user[4] else "❌ Not Active"}
📊 Daily Withdrawn: {user[6]}/{MAX_DAILY_WITHDRAW} 🟡⭐
"""

    bot.edit_message_text(
        text, 
        call.message.chat.id, 
        call.message.message_id, 
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= LEADERBOARD =================

@bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def leaderboard(call):
    cursor.execute("SELECT user_id, stars FROM users_wallet ORDER BY stars DESC LIMIT 10")
    users = cursor.fetchall()

    text = "🏆🎖 **TOP USERS**\n\n"

    for admin in ADMIN_IDS:
        text += f"👑 `{admin}` - ∞ 🟡⭐ (Admin)\n"

    for u in users:
        if u[0] not in ADMIN_IDS:
            text += f"`{u[0]}` - {u[1]} 🟡⭐\n"

    bot.edit_message_text(
        text, 
        call.message.chat.id, 
        call.message.message_id, 
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= WITHDRAW =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw")
def withdraw(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)

    if user[4] == 0:
        bot.answer_callback_query(call.id, "🚫 Premium required to withdraw!", show_alert=True)
        return

    if user[1] < MIN_WITHDRAW:
        bot.answer_callback_query(call.id, f"❌ Minimum withdrawal is {MIN_WITHDRAW} 🟡⭐", show_alert=True)
        return
    
    if user[6] >= MAX_DAILY_WITHDRAW:
        bot.answer_callback_query(call.id, f"❌ Daily withdrawal limit ({MAX_DAILY_WITHDRAW} 🟡⭐) reached!", show_alert=True)
        return
    
    cooldown = check_cooldown(user_id, "withdraw", WITHDRAWAL_COOLDOWN)
    if cooldown > 0:
        hours = cooldown // 3600
        minutes = (cooldown % 3600) // 60
        bot.answer_callback_query(
            call.id, 
            f"⏳ Withdrawal cooldown: {hours}h {minutes}m remaining",
            show_alert=True
        )
        return

    max_allowed = MAX_DAILY_WITHDRAW - user[6]
    withdraw_amount = min(user[1], max_allowed)

    cursor.execute("""
        INSERT INTO withdraw_requests (user_id, amount, status)
        VALUES (?, ?, 'pending')
    """, (user_id, withdraw_amount))
    conn.commit()
    
    cursor.execute("""
        UPDATE users_wallet 
        SET daily_withdrawn = daily_withdrawn + ? 
        WHERE user_id = ?
    """, (withdraw_amount, user_id))
    conn.commit()
    
    log_action(user_id, "withdraw")

    bot.answer_callback_query(
        call.id, 
        f"✅ Withdrawal request for {withdraw_amount} 🟡⭐ submitted!\nIt will be processed automatically within 5 minutes.",
        show_alert=True
    )

# ================= REFERRAL LINK =================

@bot.callback_query_handler(func=lambda c: c.data == "refer")
def refer(call):
    user_id = call.from_user.id
    bot_name = bot.get_me().username
    refer_link = f"https://t.me/{bot_name}?start={user_id}"
    
    bot.edit_message_text(
        f"📨🔥 **Refer & Earn**\n\n"
        f"Share your referral link and earn 5 🟡⭐ for each friend who joins!\n\n"
        f"🔗 **Your link:**\n`{refer_link}`\n\n"
        f"📊 Total Referrals: {get_wallet(user_id)[3]}",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= PREMIUM =================

@bot.callback_query_handler(func=lambda c: c.data == "premium")
def premium(call):
    cursor.execute("UPDATE users_wallet SET premium=1 WHERE user_id=?", (call.from_user.id,))
    conn.commit()
    bot.answer_callback_query(call.id, "💎 Premium Activated!")
    
    bot.edit_message_text(
        "✅ Premium activated! You now have access to withdrawals and exclusive features!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu()
    )

# ================= BUY STARS MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
def buy_menu(call):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💫 Buy Stars with Telegram Stars", callback_data="buy_show"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="back"))
    
    bot.edit_message_text(
        "🟡💰 **Purchase Options**\n\n"
        "Choose how you want to buy stars:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "buy_show")
def buy_show(call):
    markup = InlineKeyboardMarkup()
    
    for stars, price in STAR_PACKAGES.items():
        markup.add(InlineKeyboardButton(
            f"💫 {stars} Stars - {price} ⭐️", 
            callback_data=f"buy_{stars}"
        ))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="buy_menu"))
    
    bot.edit_message_text(
        "✨ **Purchase Stars with Telegram Stars!** ✨\n\n"
        "Choose a package below:\n"
        "💎 More stars = bigger discount!\n\n"
        "⬇️ Click to buy:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_buy(call):
    stars = call.data.split("_")[1]
    price = STAR_PACKAGES[stars]
    
    prices = [LabeledPrice(label=f"{stars} Stars", amount=price)]
    
    bot.send_invoice(
        call.message.chat.id,
        title=f"✨ {stars} Stars Package",
        description=f"Get {stars} 🟡⭐ stars for your wallet!\n" +
                   f"💰 Price: {price} ⭐️ Telegram Stars",
        invoice_payload=f"buy_stars_{stars}",
        provider_token="",  # Empty for Telegram Stars!
        currency="XTR",
        prices=prices,
        start_parameter="create_invoice_stars",
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False
    )

@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def payment_success(message):
    payload = message.successful_payment.invoice_payload
    stars_purchased = int(payload.split("_")[2])
    amount_paid = message.successful_payment.total_amount
    
    user_id = message.from_user.id
    
    add_stars(user_id, stars_purchased)
    
    cursor.execute("""
        INSERT INTO payments (user_id, telegram_payment_charge_id, stars_purchased, amount_paid)
        VALUES (?, ?, ?, ?)
    """, (user_id, message.successful_payment.telegram_payment_charge_id, stars_purchased, amount_paid))
    conn.commit()
    
    bot.send_message(
        user_id,
        f"✅ **Payment Successful!**\n\n"
        f"✨ Added: {stars_purchased} 🟡⭐ stars to your wallet\n"
        f"💳 Payment ID: `{message.successful_payment.telegram_payment_charge_id}`\n\n"
        f"💰 New balance: {get_wallet(user_id)[1]} 🟡⭐",
        parse_mode="Markdown"
    )
    
    for admin in ADMIN_IDS:
        try:
            bot.send_message(
                admin,
                f"💰 **New Purchase!**\n"
                f"User: `{user_id}`\n"
                f"Stars: {stars_purchased} 🟡⭐\n"
                f"Paid: {amount_paid} ⭐️"
            )
        except:
            pass

# ================= BACK BUTTON =================

@bot.callback_query_handler(func=lambda c: c.data == "back")
def back(call):
    bot.edit_message_text(
        "🔥 Welcome back to Gold Ultimate Bot!",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu()
    )

# ================= ADMIN DAILY BONUS =================

def daily_admin_bonus():
    while True:
        time.sleep(86400)  # 24 hours
        reset_daily_withdrawals()
        for admin in ADMIN_IDS:
            cursor.execute("UPDATE users_wallet SET stars = stars + 100 WHERE user_id=?", (admin,))
        conn.commit()
        print("✅ Admin daily bonus added")

threading.Thread(target=daily_admin_bonus, daemon=True).start()

# ================= GITHUB BACKUP =================

def backup_to_github():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return

    try:
        with open("bot.db", "rb") as f:
            content = base64.b64encode(f.read()).decode()

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}

        r = requests.get(url, headers=headers)
        sha = None
        if r.status_code == 200:
            sha = r.json()["sha"]

        data = {
            "message": f"Auto backup {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "content": content
        }

        if sha:
            data["sha"] = sha

        response = requests.put(url, json=data, headers=headers)
        if response.status_code in [200, 201]:
            print(f"✅ Database backed up to GitHub: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print(f"❌ GitHub backup failed: {response.status_code}")
    except Exception as e:
        print(f"❌ GitHub backup error: {e}")

def backup_loop():
    while True:
        time.sleep(3600)  # Every hour
        backup_to_github()

if GITHUB_TOKEN and GITHUB_REPO:
    threading.Thread(target=backup_loop, daemon=True).start()
    print("✅ GitHub backup system started")

# ================= WEBHOOK SETUP =================

def setup_webhook():
    """Setup webhook automatically using Render URL"""
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    
    if render_url:
        webhook_url = f"{render_url}/{TOKEN}"
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=webhook_url)
        print(f"✅ Webhook set to: {webhook_url}")
        return True
    else:
        print("⚠️ RENDER_EXTERNAL_URL not found. Running in polling mode...")
        return False

# ================= FLASK WEBHOOK ENDPOINT =================

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates"""
    try:
        json_str = request.get_data().decode('UTF-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return 'OK', 200
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return 'ERROR', 500

# ================= MAIN EXECUTION =================

if __name__ == "__main__":
    print("🚀 Starting Gold Ultimate Bot...")
    print("=" * 50)
    print("💰 Earning System: Active")
    print("👥 Referral System: Active")
    print("💳 Withdrawal System: Active")
    print("⭐ Telegram Stars: Active")
    print("🛡️ Anti-Spam Cooldown: Active")
    print("💾 GitHub Backup: " + ("Active" if GITHUB_TOKEN and GITHUB_REPO else "Disabled"))
    print("=" * 50)
    
    # Setup webhook or polling
    using_webhook = setup_webhook()
    
    # Start keep-alive service
    global keep_alive
    if RENDER_EXTERNAL_URL:
        health_url = f"{RENDER_EXTERNAL_URL}/health"
        keep_alive = KeepAliveService(health_url)
        keep_alive.start()
    else:
        # Local development - ping localhost
        keep_alive = KeepAliveService(f"http://localhost:{os.environ.get('PORT', 10000)}/health")
        keep_alive.start()
    
    # Start Flask server
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
