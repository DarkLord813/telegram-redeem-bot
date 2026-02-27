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
GITHUB_FILE_PATH = "pulse_profit.db"

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

# ================= TELEGRAM STARS WITHDRAWAL RATE =================
STARS_TO_XTR_RATE = 1  # 1 in-app star = 1 Telegram Star (XTR)

# ================= DATABASE =================
conn = sqlite3.connect("pulse_profit.db", check_same_thread=False)
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
    withdrawal_type TEXT DEFAULT 'admin',  -- 'admin', 'stars'
    status TEXT DEFAULT 'pending',
    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_time TIMESTAMP DEFAULT NULL,
    transaction_id TEXT
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

# Add new tables for task system
cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    task_type TEXT,
    task_data TEXT,
    reward INTEGER,
    max_completions INTEGER DEFAULT -1,
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
    verified INTEGER DEFAULT 0,
    verified_by INTEGER,
    verified_at TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
)
""")

# Add backup log table
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
        'service': 'Pulse Profit Bot',
        'timestamp': time.time()
    })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'timestamp': time.time(),
        'pings': keep_alive.ping_count if 'keep_alive' in globals() else 0
    }), 200

# ================= GITHUB BACKUP SYSTEM =================

def backup_to_github(backup_type="auto", details=""):
    """Backup database to GitHub"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return False

    try:
        print(f"🔄 Starting GitHub backup ({backup_type})...")
        
        with open("pulse_profit.db", "rb") as f:
            content = base64.b64encode(f.read()).decode()

        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}

        # Get current file SHA if exists
        r = requests.get(url, headers=headers)
        sha = None
        if r.status_code == 200:
            sha = r.json()["sha"]

        # Create backup with timestamp
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data = {
            "message": f"Backup {timestamp} - {backup_type}",
            "content": content
        }

        if sha:
            data["sha"] = sha

        response = requests.put(url, json=data, headers=headers)
        
        if response.status_code in [200, 201]:
            print(f"✅ GitHub backup successful: {timestamp}")
            
            # Log backup
            cursor.execute("""
                INSERT INTO backup_log (backup_type, status, details)
                VALUES (?, ?, ?)
            """, (backup_type, "success", details))
            conn.commit()
            
            return True
        else:
            print(f"❌ GitHub backup failed: {response.status_code}")
            
            # Log failure
            cursor.execute("""
                INSERT INTO backup_log (backup_type, status, details)
                VALUES (?, ?, ?)
            """, (backup_type, "failed", f"Status code: {response.status_code}"))
            conn.commit()
            
            return False
            
    except Exception as e:
        print(f"❌ GitHub backup error: {e}")
        
        # Log error
        try:
            cursor.execute("""
                INSERT INTO backup_log (backup_type, status, details)
                VALUES (?, ?, ?)
            """, (backup_type, "error", str(e)))
            conn.commit()
        except:
            pass
        
        return False

def backup_loop():
    """Hourly automatic backup"""
    while True:
        time.sleep(3600)  # Every hour
        backup_to_github("hourly", "Automatic hourly backup")

# Start backup threads if configured
if GITHUB_TOKEN and GITHUB_REPO:
    threading.Thread(target=backup_loop, daemon=True).start()
    print("✅ GitHub hourly backup system started")

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

def add_stars(user_id, amount, trigger_backup=True):
    """Add stars to user wallet with optional backup"""
    cursor.execute("""
        UPDATE users_wallet
        SET stars = stars + ?, total_earned = total_earned + ?
        WHERE user_id=?
    """, (amount, amount, user_id))
    conn.commit()
    
    # Trigger backup on significant earnings (every 100 stars)
    if trigger_backup:
        user = get_wallet(user_id)
        if user[1] % 100 == 0 or amount >= 50:
            threading.Thread(target=backup_to_github, args=("earning", f"User {user_id} earned {amount} stars"), daemon=True).start()

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
    
    # Backup after reset
    if GITHUB_TOKEN and GITHUB_REPO:
        threading.Thread(target=backup_to_github, args=("daily_reset", "Daily withdrawal limits reset"), daemon=True).start()

def get_user_display_name(user_id):
    """Get user's display name (first name + username if available)"""
    try:
        user_info = bot.get_chat_member(user_id, user_id).user
        name = user_info.first_name
        if user_info.username:
            name += f" (@{user_info.username})"
        return name
    except:
        return f"User {str(user_id)[:6]}..."

def is_admin(user_id):
    """Check if user is admin"""
    return user_id in ADMIN_IDS

# ================= AUTO WITHDRAWAL PROCESSOR =================

def process_withdrawals():
    """Automatically process pending withdrawals"""
    while True:
        time.sleep(300)  # Check every 5 minutes
        
        processed_count = 0
        cursor.execute("""
            SELECT id, user_id, amount, withdrawal_type FROM withdraw_requests 
            WHERE status = 'pending' 
            ORDER BY request_time ASC
        """)
        pending = cursor.fetchall()
        
        for req_id, user_id, amount, withdrawal_type in pending:
            user = get_wallet(user_id)
            
            if user[1] >= amount:
                # Deduct stars from wallet
                cursor.execute("""
                    UPDATE users_wallet 
                    SET stars = stars - ? 
                    WHERE user_id = ?
                """, (amount, user_id))
                
                if withdrawal_type == "stars":
                    # Process Telegram Stars withdrawal
                    try:
                        # Create invoice to send stars to user
                        prices = [LabeledPrice(label=f"Withdrawal of {amount} Stars", amount=amount)]
                        
                        bot.send_invoice(
                            user_id,
                            title=f"⚡ Pulse Profit Withdrawal",
                            description=f"Your withdrawal of {amount} 🟡⭐ stars",
                            invoice_payload=f"withdraw_{req_id}",
                            provider_token="",  # Empty for Telegram Stars!
                            currency="XTR",
                            prices=prices,
                            start_parameter="withdraw_stars",
                            need_name=False,
                            need_phone_number=False,
                            need_email=False,
                            need_shipping_address=False,
                            is_flexible=False
                        )
                        
                        # Generate transaction ID
                        transaction_id = f"W{int(time.time())}{req_id}"
                        
                        cursor.execute("""
                            UPDATE withdraw_requests 
                            SET status = 'approved', processed_time = ?, transaction_id = ?
                            WHERE id = ?
                        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), transaction_id, req_id))
                        
                        conn.commit()
                        processed_count += 1
                        
                        # Notify user
                        try:
                            bot.send_message(
                                user_id, 
                                f"✅ **Stars Withdrawal Sent!**\n\n"
                                f"Amount: {amount} ⭐️ Telegram Stars\n"
                                f"Transaction ID: `{transaction_id}`\n\n"
                                f"Check your Telegram Stars balance!",
                                parse_mode="Markdown"
                            )
                        except:
                            pass
                            
                    except Exception as e:
                        print(f"❌ Stars withdrawal error: {e}")
                        cursor.execute("""
                            UPDATE withdraw_requests 
                            SET status = 'failed', processed_time = ?
                            WHERE id = ?
                        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
                        conn.commit()
                        
                else:  # admin withdrawal
                    cursor.execute("""
                        UPDATE withdraw_requests 
                        SET status = 'approved', processed_time = ?
                        WHERE id = ?
                    """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
                    
                    conn.commit()
                    processed_count += 1
                    
                    # Notify user
                    try:
                        bot.send_message(
                            user_id, 
                            f"✅ **Withdrawal Approved!**\n\n"
                            f"Amount: {amount} 🟡⭐\n"
                            f"Your withdrawal has been approved and will be processed manually.",
                            parse_mode="Markdown"
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
                        f"❌ **Withdrawal Rejected**\n\n"
                        f"Amount: {amount} 🟡⭐\n"
                        f"Reason: Insufficient balance",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        if processed_count > 0 and GITHUB_TOKEN and GITHUB_REPO:
            # Backup after processing withdrawals
            threading.Thread(target=backup_to_github, args=("withdrawal", f"Processed {processed_count} withdrawals"), daemon=True).start()

# Start withdrawal processor thread
threading.Thread(target=process_withdrawals, daemon=True).start()

# ================= MAIN MENU =================

def main_menu():
    """Create main menu keyboard with colorful buttons"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💼✨ EARN STARS 💼✨", callback_data="earn"),
        InlineKeyboardButton("📋✅ TASKS 📋✅", callback_data="show_tasks")
    )
    markup.row(
        InlineKeyboardButton("📨🔥 REFER & EARN 📨🔥", callback_data="refer"),
        InlineKeyboardButton("👤🌈 PROFILE 👤🌈", callback_data="profile")
    )
    markup.row(
        InlineKeyboardButton("🏆🎖 LEADERBOARD 🏆🎖", callback_data="leaderboard"),
        InlineKeyboardButton("💎🚀 PREMIUM 💎🚀", callback_data="premium")
    )
    markup.row(
        InlineKeyboardButton("🟡💰 BUY STARS 🟡💰", callback_data="buy_menu"),
        InlineKeyboardButton("💳🏦 WITHDRAW 💳🏦", callback_data="withdraw_menu")
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
                        add_stars(referrer_id, 5, trigger_backup=False)
                        log_action(referrer_id, "refer")
                        conn.commit()
                        
                        # Notify referrer
                        referrer_name = get_user_display_name(referrer_id)
                        user_name = get_user_display_name(user_id)
                        try:
                            bot.send_message(
                                referrer_id,
                                f"🎉 **New Referral!**\n\n"
                                f"👤 {user_name} joined using your link!\n"
                                f"💰 You earned **5** 🟡⭐!\n\n"
                                f"📊 Total Referrals: {get_wallet(referrer_id)[3]}",
                                parse_mode="Markdown"
                            )
                        except:
                            pass
                        
                        # Backup on new referral
                        if GITHUB_TOKEN and GITHUB_REPO:
                            threading.Thread(target=backup_to_github, args=("referral", f"New user {user_id} referred by {referrer_id}"), daemon=True).start()
                    else:
                        try:
                            bot.send_message(
                                referrer_id,
                                f"⏳ Please wait **{cooldown}** seconds before next referral!",
                                parse_mode="Markdown"
                            )
                        except:
                            pass
        except:
            pass

    # Welcome message
    user_name = get_user_display_name(user_id)
    welcome_text = f"""
━━━━━━━━━━━━━━━━━━━━━
⚡ **WELCOME TO PULSE PROFIT** ⚡
━━━━━━━━━━━━━━━━━━━━━

👋 Hello **{user_name}**!

━━━━━━━━━━━━━━━━━━━━━
✨ **WHAT YOU CAN DO:** ✨
━━━━━━━━━━━━━━━━━━━━━

💰 **EARN STARS** - Complete tasks and earn
📋 **TASKS** - Join channels, visit links
👥 **REFER FRIENDS** - Earn 5⭐ per referral
💎 **PREMIUM** - Unlock premium features
🟡 **BUY STARS** - Purchase with Telegram Stars
💳 **WITHDRAW** - Convert to Admin or Telegram Stars

━━━━━━━━━━━━━━━━━━━━━
💡 **Your Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

👇 **Choose an option below:** 👇
"""
    bot.send_message(
        user_id,
        welcome_text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= EARN STARS =================

@bot.callback_query_handler(func=lambda c: c.data == "earn")
def earn(call):
    user_id = call.from_user.id
    user_name = get_user_display_name(user_id)
    
    cooldown = check_cooldown(user_id, "earn", COOLDOWN_TIME)
    
    if cooldown > 0:
        minutes = cooldown // 60
        seconds = cooldown % 60
        time_text = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        
        bot.answer_callback_query(
            call.id, 
            f"⏳ Please wait {time_text} before earning again!",
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
    
    # Backup on every 10th earn
    user = get_wallet(user_id)
    if user[5] % 10 == 0 and GITHUB_TOKEN and GITHUB_REPO:
        threading.Thread(target=backup_to_github, args=("earn_milestone", f"User {user_id} completed {user[5]} tasks"), daemon=True).start()
    
    # Create colorful response
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **EARNED STARS!** ✅
━━━━━━━━━━━━━━━━━━━━━

👤 **{user_name}**

━━━━━━━━━━━━━━━━━━━━━
💰 **+{reward}** 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

📊 **New Balance:** **{user[1]}** 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

💡 _Keep earning to reach the top!_
"""
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= PROFILE =================

@bot.callback_query_handler(func=lambda c: c.data == "profile")
def profile(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)
    user_name = get_user_display_name(user_id)
    
    # Get rank
    cursor.execute("""
        SELECT COUNT(*) + 1 FROM users_wallet 
        WHERE stars > (SELECT stars FROM users_wallet WHERE user_id = ?)
    """, (user_id,))
    rank = cursor.fetchone()[0]
    
    # Calculate progress to next rank
    cursor.execute("""
        SELECT stars FROM users_wallet 
        WHERE stars > (SELECT stars FROM users_wallet WHERE user_id = ?)
        ORDER BY stars ASC LIMIT 1
    """, (user_id,))
    next_rank = cursor.fetchone()
    
    if next_rank:
        next_stars = next_rank[0]
        stars_needed = next_stars - user[1]
        progress = (user[1] / next_stars) * 100 if next_stars > 0 else 0
    else:
        stars_needed = 0
        progress = 100
    
    # Create progress bar
    progress_length = int(progress // 10)
    progress_bar = "🟩" * progress_length + "⬜" * (10 - progress_length)
    
    # Calculate level based on stars
    level = user[1] // 100 + 1
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
    ⚡ **PULSE PROFIT PROFILE** ⚡
━━━━━━━━━━━━━━━━━━━━━

👤 **User:** {user_name}
🏆 **Global Rank:** #{rank}
📊 **Level:** {level}

━━━━━━━━━━━━━━━━━━━━━
📊 **STATISTICS** 📊
━━━━━━━━━━━━━━━━━━━━━

⭐ **Balance:** `{user[1]:,}` 🟡
💰 **Total Earned:** `{user[2]:,}` 🟡
👥 **Referrals:** `{user[3]}`
🎯 **Tasks Done:** `{user[5]}`

━━━━━━━━━━━━━━━━━━━━━
📈 **PROGRESS** 📈
━━━━━━━━━━━━━━━━━━━━━
{progress_bar} `{progress:.1f}%`

"""
    
    if stars_needed > 0:
        text += f"🎯 **Next Rank:** Need **{stars_needed}** more 🟡⭐\n"
    
    text += f"💎 **Premium:** {'✅ ACTIVE' if user[4] else '❌ Not Active'}\n"
    text += f"📊 **Daily Withdrawn:** `{user[6]}/{MAX_DAILY_WITHDRAW}` 🟡\n"
    text += "━━━━━━━━━━━━━━━━━━━━━"
    
    # Create colorful profile buttons
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💰 EARN MORE 💰", callback_data="earn"),
        InlineKeyboardButton("📋 TASKS 📋", callback_data="show_tasks")
    )
    markup.row(
        InlineKeyboardButton("👥 REFERRALS 👥", callback_data="refer"),
        InlineKeyboardButton("🏆 LEADERBOARD 🏆", callback_data="leaderboard")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK TO MENU 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= LEADERBOARD WITH NAMES =================

@bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def leaderboard(call):
    # Get top 10 users by stars
    cursor.execute("""
        SELECT user_id, stars, total_earned, referrals 
        FROM users_wallet 
        ORDER BY stars DESC 
        LIMIT 10
    """)
    top_users = cursor.fetchall()
    
    # Create colorful leaderboard text
    text = """
━━━━━━━━━━━━━━━━━━━━━
⚡ **PULSE PROFIT LEADERBOARD** ⚡
━━━━━━━━━━━━━━━━━━━━━

"""
    
    # Medal emojis for top 3
    medals = ["🥇 **GOLD**", "🥈 **SILVER**", "🥉 **BRONZE**"]
    
    # Add admin section (always on top)
    if ADMIN_IDS:
        text += "👑 **━━━━ ADMIN ZONE ━━━━** 👑\n"
        for admin_id in ADMIN_IDS:
            admin_name = get_user_display_name(admin_id)
            text += f"👑 **{admin_name}**\n"
            text += "└ ∞ 🟡⭐ (Unlimited)\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
    
    text += "🌟 **TOP EARNERS** 🌟\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Process top users
    rank = 1
    for user_id, stars, total_earned, referrals in top_users:
        if user_id in ADMIN_IDS:
            continue  # Skip admins (already shown)
        
        user_name = get_user_display_name(user_id)
        
        # Choose medal or number
        if rank <= 3:
            rank_display = medals[rank-1]
        else:
            rank_display = f"**#{rank}**"
        
        # Colorful progress bar based on stars
        progress_length = min(stars // 10, 10)
        progress_bar = "🟩" * progress_length + "⬜" * (10 - progress_length)
        
        # Format stars with commas
        stars_formatted = f"{stars:,}"
        
        text += f"{rank_display} **{user_name}**\n"
        text += f"├ 💰 Stars: **{stars_formatted}** 🟡\n"
        text += f"├ 📊 {progress_bar}\n"
        text += f"└ 👥 Referrals: **{referrals}**\n\n"
        
        rank += 1
        if rank > 10:
            break
    
    # Add total stats
    cursor.execute("SELECT COUNT(*) FROM users_wallet")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(stars) FROM users_wallet")
    total_stars = cursor.fetchone()[0] or 0
    
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "📊 **STATISTICS** 📊\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"👥 **Total Users:** `{total_users:,}`\n"
    text += f"💰 **Total Stars:** `{total_stars:,}` 🟡\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += "💡 _Keep earning to reach the top!_"
    
    # Create colorful navigation buttons
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔄 REFRESH 🔄", callback_data="leaderboard"),
        InlineKeyboardButton("📊 MY STATS 📊", callback_data="profile")
    )
    markup.row(
        InlineKeyboardButton("💰 EARN STARS 💰", callback_data="earn"),
        InlineKeyboardButton("👥 REFER 👥", callback_data="refer")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK TO MENU 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= WITHDRAWAL MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_menu")
def withdraw_menu(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)
    is_admin_user = is_admin(user_id)
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
💳 **WITHDRAWAL OPTIONS** 💳
━━━━━━━━━━━━━━━━━━━━━

👤 **Your Balance:** {user[1]} 🟡⭐
📊 **Daily Withdrawn:** {user[6]}/{MAX_DAILY_WITHDRAW} 🟡

━━━━━━━━━━━━━━━━━━━━━
💰 **WITHDRAWAL METHODS** 💰
━━━━━━━━━━━━━━━━━━━━━

"""
    
    if is_admin_user:
        text += "👑 **ADMIN PRIVILEGES**\n"
        text += "• No premium required\n"
        text += "• No daily limits\n"
        text += "• Instant approval\n\n"
    
    text += """⭐ **Telegram Stars Withdrawal**
• 1 🟡⭐ = 1 ⭐️ Telegram Star
• Instant delivery to your wallet
• Available to all users
• Minimum: 50 🟡⭐

💼 **Admin Withdrawal**
• Manual processing
• For special requests only
• Contact admins for details

━━━━━━━━━━━━━━━━━━━━━
👇 **Choose withdrawal type:** 👇
"""
    
    markup = InlineKeyboardMarkup()
    
    if is_admin_user:
        # Admin buttons - no premium required, no limits
        markup.row(
            InlineKeyboardButton("⭐ WITHDRAW AS STARS ⭐", callback_data="withdraw_stars_menu"),
            InlineKeyboardButton("💼 ADMIN WITHDRAW 💼", callback_data="withdraw_admin_menu")
        )
    else:
        # Regular user buttons - premium check
        if user[4] == 1:
            markup.row(
                InlineKeyboardButton("⭐ WITHDRAW AS STARS ⭐", callback_data="withdraw_stars_menu"),
                InlineKeyboardButton("💼 ADMIN REQUEST 💼", callback_data="withdraw_admin")
            )
        else:
            markup.row(
                InlineKeyboardButton("⭐ WITHDRAW AS STARS ⭐", callback_data="withdraw_stars_menu")
            )
            text += "\n⚠️ **Note:** Admin withdrawal requires Premium membership!\n"
    
    markup.row(
        InlineKeyboardButton("🔙 BACK TO MENU 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= STARS WITHDRAWAL MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_stars_menu")
def withdraw_stars_menu(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)
    
    # Calculate available amount (respect daily limit for non-admins)
    if is_admin(user_id):
        max_allowed = user[1]  # No limit for admins
    else:
        max_allowed = min(user[1], MAX_DAILY_WITHDRAW - user[6])
    
    if max_allowed < MIN_WITHDRAW:
        text = f"""
━━━━━━━━━━━━━━━━━━━━━
❌ **CANNOT WITHDRAW** ❌
━━━━━━━━━━━━━━━━━━━━━

📊 **Your Balance:** {user[1]} 🟡⭐
📉 **Available:** {max_allowed} 🟡⭐
⚠️ **Minimum:** {MIN_WITHDRAW} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
💡 Earn more stars to reach minimum!
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💰 EARN STARS 💰", callback_data="earn"),
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
        )
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return
    
    # Create preset withdrawal amounts
    presets = [50, 100, 200, 500, 1000]
    available_presets = [p for p in presets if p <= max_allowed]
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
⭐ **STARS WITHDRAWAL** ⭐
━━━━━━━━━━━━━━━━━━━━━

📊 **Your Balance:** {user[1]} 🟡⭐
📤 **Available:** {max_allowed} 🟡⭐
⚡ **Rate:** 1 🟡⭐ = 1 ⭐️ Telegram Star

━━━━━━━━━━━━━━━━━━━━━
💡 **Choose amount or enter custom:**
━━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = InlineKeyboardMarkup()
    
    # Add preset buttons in rows of 2
    row = []
    for i, amount in enumerate(available_presets):
        row.append(InlineKeyboardButton(f"{amount} ⭐", callback_data=f"withdraw_stars_{amount}"))
        if len(row) == 2 or i == len(available_presets) - 1:
            markup.row(*row)
            row = []
    
    markup.row(
        InlineKeyboardButton("✏️ CUSTOM AMOUNT ✏️", callback_data="withdraw_stars_custom")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_stars_"))
def withdraw_stars_amount(call):
    user_id = call.from_user.id
    
    if call.data == "withdraw_stars_custom":
        # Ask for custom amount
        bot.answer_callback_query(call.id, "Please enter the amount you want to withdraw:", show_alert=False)
        
        # Store in session
        cursor.execute("""
            INSERT OR REPLACE INTO user_actions (user_id, action_type, action_time)
            VALUES (?, ?, ?)
        """, (user_id, "awaiting_stars_withdraw", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        
        bot.edit_message_text(
            "✏️ **Enter the amount of 🟡⭐ you want to withdraw as Telegram Stars:**\n\n"
            f"Minimum: {MIN_WITHDRAW}\n"
            "Send a number:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        return
    
    amount = int(call.data.replace("withdraw_stars_", ""))
    process_stars_withdrawal(call.message.chat.id, call.message.message_id, user_id, amount, call)

def process_stars_withdrawal(chat_id, message_id, user_id, amount, call=None):
    """Process stars withdrawal request"""
    user = get_wallet(user_id)
    
    # Check minimum
    if amount < MIN_WITHDRAW:
        if call:
            bot.answer_callback_query(call.id, f"❌ Minimum withdrawal is {MIN_WITHDRAW} 🟡⭐", show_alert=True)
        return
    
    # Check balance
    if user[1] < amount:
        if call:
            bot.answer_callback_query(call.id, "❌ Insufficient balance!", show_alert=True)
        return
    
    # Check daily limit for non-admins
    if not is_admin(user_id):
        if user[6] + amount > MAX_DAILY_WITHDRAW:
            remaining = MAX_DAILY_WITHDRAW - user[6]
            if call:
                bot.answer_callback_query(call.id, f"❌ Daily limit exceeded! You can withdraw {remaining} more today.", show_alert=True)
            return
    
    # Create withdrawal request
    cursor.execute("""
        INSERT INTO withdraw_requests (user_id, amount, withdrawal_type, status)
        VALUES (?, ?, 'stars', 'pending')
    """, (user_id, amount))
    conn.commit()
    
    req_id = cursor.lastrowid
    
    # Update daily withdrawn (for non-admins)
    if not is_admin(user_id):
        cursor.execute("""
            UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id = ?
        """, (amount, user_id))
        conn.commit()
    
    # Show confirmation
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **WITHDRAWAL REQUESTED** ✅
━━━━━━━━━━━━━━━━━━━━━

📤 **Amount:** {amount} 🟡⭐
⭐ **You'll receive:** {amount} Telegram Stars
🆔 **Request ID:** {req_id}

━━━━━━━━━━━━━━━━━━━━━
⏳ Your withdrawal will be processed within 5 minutes.
You'll receive the stars directly in your Telegram wallet!
━━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💰 EARN MORE 💰", callback_data="earn"),
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    
    # Trigger backup
    if GITHUB_TOKEN and GITHUB_REPO:
        threading.Thread(target=backup_to_github, args=("withdrawal_request", f"User {user_id} requested {amount} stars withdrawal"), daemon=True).start()

# ================= ADMIN WITHDRAWAL =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_admin")
def withdraw_admin(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)
    
    # Check premium for non-admins
    if not is_admin(user_id) and user[4] == 0:
        bot.answer_callback_query(
            call.id, 
            "❌ Premium membership required for admin withdrawals!", 
            show_alert=True
        )
        return
    
    # Calculate available amount
    if is_admin(user_id):
        max_allowed = user[1]  # No limit for admins
        limit_text = "∞ (Admin)"
    else:
        max_allowed = min(user[1], MAX_DAILY_WITHDRAW - user[6])
        limit_text = f"{MAX_DAILY_WITHDRAW - user[6]} 🟡⭐"
    
    if max_allowed < MIN_WITHDRAW:
        text = f"""
━━━━━━━━━━━━━━━━━━━━━
❌ **CANNOT WITHDRAW** ❌
━━━━━━━━━━━━━━━━━━━━━

📊 **Your Balance:** {user[1]} 🟡⭐
📉 **Available:** {max_allowed} 🟡⭐
⚠️ **Minimum:** {MIN_WITHDRAW} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
💡 Earn more stars to reach minimum!
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💰 EARN STARS 💰", callback_data="earn"),
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
        )
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
💼 **ADMIN WITHDRAWAL REQUEST** 💼
━━━━━━━━━━━━━━━━━━━━━

📊 **Your Balance:** {user[1]} 🟡⭐
📤 **Available Today:** {limit_text}
⚠️ **Minimum:** {MIN_WITHDRAW} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
📝 **Enter the amount you want to withdraw:**
━━━━━━━━━━━━━━━━━━━━━

💡 Send a number (e.g., 100)
"""
    
    # Store that we're waiting for withdrawal amount
    cursor.execute("""
        INSERT OR REPLACE INTO user_actions (user_id, action_type, action_time)
        VALUES (?, ?, ?)
    """, (user_id, "awaiting_admin_withdraw", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= HANDLE CUSTOM WITHDRAWAL AMOUNTS =================

@bot.message_handler(func=lambda message: True)
def handle_withdrawal_amount(message):
    """Handle custom withdrawal amounts"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    
    # Check if we're waiting for a withdrawal amount
    cursor.execute("""
        SELECT action_type FROM user_actions 
        WHERE user_id = ? AND action_type IN ('awaiting_stars_withdraw', 'awaiting_admin_withdraw')
        ORDER BY action_time DESC LIMIT 1
    """, (user_id,))
    
    result = cursor.fetchone()
    if not result:
        return False
    
    action_type = result[0]
    
    try:
        amount = int(text)
        if amount < MIN_WITHDRAW:
            bot.send_message(
                chat_id,
                f"❌ Minimum withdrawal is {MIN_WITHDRAW} 🟡⭐\nPlease try again:",
                parse_mode="Markdown"
            )
            return True
        
        user = get_wallet(user_id)
        
        if amount > user[1]:
            bot.send_message(
                chat_id,
                "❌ Insufficient balance!\nPlease try again:",
                parse_mode="Markdown"
            )
            return True
        
        # Check daily limit for non-admins
        if not is_admin(user_id):
            if user[6] + amount > MAX_DAILY_WITHDRAW:
                remaining = MAX_DAILY_WITHDRAW - user[6]
                bot.send_message(
                    chat_id,
                    f"❌ Daily limit exceeded! You can withdraw {remaining} more today.\nPlease try again:",
                    parse_mode="Markdown"
                )
                return True
        
        if action_type == "awaiting_stars_withdraw":
            # Process stars withdrawal
            cursor.execute("""
                INSERT INTO withdraw_requests (user_id, amount, withdrawal_type, status)
                VALUES (?, ?, 'stars', 'pending')
            """, (user_id, amount))
            conn.commit()
            
            # Update daily withdrawn for non-admins
            if not is_admin(user_id):
                cursor.execute("""
                    UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id = ?
                """, (amount, user_id))
                conn.commit()
            
            bot.send_message(
                chat_id,
                f"✅ **Withdrawal Requested!**\n\n"
                f"Amount: {amount} 🟡⭐\n"
                f"You'll receive: {amount} ⭐️ Telegram Stars\n\n"
                f"Your withdrawal will be processed within 5 minutes.",
                parse_mode="Markdown"
            )
            
        else:  # admin withdrawal
            cursor.execute("""
                INSERT INTO withdraw_requests (user_id, amount, withdrawal_type, status)
                VALUES (?, ?, 'admin', 'pending')
            """, (user_id, amount))
            conn.commit()
            
            # Update daily withdrawn for non-admins
            if not is_admin(user_id):
                cursor.execute("""
                    UPDATE users_wallet SET daily_withdrawn = daily_withdrawn + ? WHERE user_id = ?
                """, (amount, user_id))
                conn.commit()
            
            # Notify admins
            user_name = get_user_display_name(user_id)
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(
                        admin_id,
                        f"🔔 **New Admin Withdrawal Request**\n\n"
                        f"👤 User: {user_name}\n"
                        f"🆔 ID: `{user_id}`\n"
                        f"💰 Amount: {amount} 🟡⭐\n\n"
                        f"Use `/approve_withdraw {user_id} {amount}` to approve",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            
            bot.send_message(
                chat_id,
                f"✅ **Withdrawal Requested!**\n\n"
                f"Amount: {amount} 🟡⭐\n"
                f"Your request has been sent to admins for approval.\n"
                f"You'll be notified when it's processed.",
                parse_mode="Markdown"
            )
        
        # Clear the waiting state
        cursor.execute("DELETE FROM user_actions WHERE user_id=? AND action_type=?", (user_id, action_type))
        conn.commit()
        
        # Trigger backup
        if GITHUB_TOKEN and GITHUB_REPO:
            threading.Thread(target=backup_to_github, args=("withdrawal_request", f"User {user_id} requested {amount} {action_type}"), daemon=True).start()
        
        return True
        
    except ValueError:
        bot.send_message(
            chat_id,
            "❌ Please enter a valid number!",
            parse_mode="Markdown"
        )
        return True

# ================= ADMIN APPROVAL COMMANDS =================

@bot.message_handler(commands=['approve_withdraw'])
def approve_withdraw(message):
    """Admin command to approve withdrawal"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    try:
        parts = message.text.split()
        target_user = int(parts[1])
        amount = int(parts[2])
        
        # Find pending request
        cursor.execute("""
            SELECT id FROM withdraw_requests 
            WHERE user_id = ? AND amount = ? AND status = 'pending' AND withdrawal_type = 'admin'
            ORDER BY request_time DESC LIMIT 1
        """, (target_user, amount))
        
        result = cursor.fetchone()
        if not result:
            bot.send_message(message.chat.id, "❌ No matching pending request found!")
            return
        
        req_id = result[0]
        
        # Process approval
        cursor.execute("""
            UPDATE users_wallet SET stars = stars - ? WHERE user_id = ?
        """, (amount, target_user))
        
        cursor.execute("""
            UPDATE withdraw_requests SET status = 'approved', processed_time = ?
            WHERE id = ?
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), req_id))
        conn.commit()
        
        # Notify user
        try:
            bot.send_message(
                target_user,
                f"✅ **Withdrawal Approved!**\n\n"
                f"Amount: {amount} 🟡⭐\n"
                f"Your admin withdrawal request has been approved!\n"
                f"Please contact admins for payment details.",
                parse_mode="Markdown"
            )
        except:
            pass
        
        bot.send_message(message.chat.id, f"✅ Withdrawal approved for user {target_user}!")
        
        # Trigger backup
        if GITHUB_TOKEN and GITHUB_REPO:
            threading.Thread(target=backup_to_github, args=("withdrawal_approved", f"Admin approved {amount} for user {target_user}"), daemon=True).start()
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Usage: /approve_withdraw [user_id] [amount]")

# ================= TASKS SYSTEM =================

@bot.callback_query_handler(func=lambda c: c.data == "show_tasks")
def show_tasks(call):
    cursor.execute("SELECT * FROM tasks WHERE active=1 ORDER BY created_at DESC")
    tasks = cursor.fetchall()
    
    if not tasks:
        text = """
━━━━━━━━━━━━━━━━━━━━━
📋 **NO TASKS AVAILABLE** 📋
━━━━━━━━━━━━━━━━━━━━━

😔 There are no tasks available right now.

💡 Check back later for new earning opportunities!

━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💰 EARN STARS 💰", callback_data="earn"),
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return
    
    text = """
━━━━━━━━━━━━━━━━━━━━━
📋 **AVAILABLE TASKS** 📋
━━━━━━━━━━━━━━━━━━━━━

👇 Click a task to view details:
\n
"""
    
    markup = InlineKeyboardMarkup()
    for task in tasks:
        task_id, task_name, task_type, task_data, reward, max_comp, active, created_by, created_at = task
        
        # Choose emoji based on task type
        if task_type == "join_channel":
            emoji = "📢"
        elif task_type == "join_group":
            emoji = "👥"
        elif task_type == "visit_link":
            emoji = "🔗"
        elif task_type == "watch_video":
            emoji = "🎥"
        else:
            emoji = "📋"
        
        markup.add(InlineKeyboardButton(
            f"{emoji} {task_name[:30]} - {reward}🟡⭐",
            callback_data=f"task_details_{task_id}"
        ))
    
    markup.row(
        InlineKeyboardButton("💰 EARN STARS 💰", callback_data="earn"),
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("task_details_"))
def task_details(call):
    task_id = int(call.data.replace("task_details_", ""))
    
    cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        bot.answer_callback_query(call.id, "❌ Task not found!", show_alert=True)
        return
    
    task_id, task_name, task_type, task_data, reward, max_comp, active, created_by, created_at = task
    
    # Check if user already completed this task
    cursor.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?", (call.from_user.id, task_id))
    existing = cursor.fetchone()
    
    # Task type emoji
    type_emoji = {
        "join_channel": "📢",
        "join_group": "👥",
        "visit_link": "🔗",
        "watch_video": "🎥"
    }.get(task_type, "📋")
    
    type_name = {
        "join_channel": "Join Channel",
        "join_group": "Join Group",
        "visit_link": "Visit Link",
        "watch_video": "Watch Video"
    }.get(task_type, task_type)
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
{type_emoji} **TASK DETAILS** {type_emoji}
━━━━━━━━━━━━━━━━━━━━━

📋 **{task_name}**

━━━━━━━━━━━━━━━━━━━━━
💰 **Reward:** {reward} 🟡⭐
📌 **Type:** {type_name}
━━━━━━━━━━━━━━━━━━━━━

"""
    
    if task_type == "join_channel":
        text += f"🔗 **Channel:** {task_data}\n\n"
        text += "✅ **How to complete:**\n"
        text += "1. Join the channel above\n"
        text += "2. Click 'Verify & Claim'\n"
        text += "3. Reward will be added automatically!\n\n"
    elif task_type == "join_group":
        text += f"👥 **Group:** {task_data}\n\n"
        text += "✅ **How to complete:**\n"
        text += "1. Join the group above\n"
        text += "2. Click 'Verify & Claim'\n"
        text += "3. Reward will be added automatically!\n\n"
    elif task_type == "visit_link":
        text += f"🔗 **Link:** {task_data}\n\n"
        text += "✅ **How to complete:**\n"
        text += "1. Visit the link above\n"
        text += "2. Click 'Submit for Verification'\n"
        text += "3. Admin will verify and add reward\n\n"
    
    if existing:
        status = existing[4]  # verified field
        if status == 1:
            text += "✅ **Status:** Already Completed ✓"
        elif status == 0:
            text += "⏳ **Status:** Pending Verification"
        else:
            text += "❌ **Status:** Rejected"
    
    text += "━━━━━━━━━━━━━━━━━━━━━"
    
    markup = InlineKeyboardMarkup()
    
    if not existing:
        if task_type in ["join_channel", "join_group"]:
            markup.row(
                InlineKeyboardButton("🔗 JOIN NOW 🔗", url=task_data),
                InlineKeyboardButton("✅ VERIFY ✅", callback_data=f"claim_task_{task_id}")
            )
        else:
            markup.row(
                InlineKeyboardButton("🔗 VISIT LINK 🔗", url=task_data),
                InlineKeyboardButton("📝 SUBMIT 📝", callback_data=f"claim_task_{task_id}")
            )
    elif existing[4] == 1:
        markup.row(
            InlineKeyboardButton("✅ COMPLETED ✅", callback_data="noop")
        )
    
    markup.row(
        InlineKeyboardButton("📋 ALL TASKS 📋", callback_data="show_tasks"),
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("claim_task_"))
def claim_task(call):
    user_id = call.from_user.id
    task_id = int(call.data.replace("claim_task_", ""))
    
    cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        bot.answer_callback_query(call.id, "❌ Task not found!", show_alert=True)
        return
    
    task_name, task_type, task_data, reward = task[1], task[2], task[3], task[4]
    
    # Check if already completed
    cursor.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id))
    existing = cursor.fetchone()
    
    if existing:
        bot.answer_callback_query(call.id, "❌ You already completed this task!", show_alert=True)
        return
    
    if task_type in ["join_channel", "join_group"]:
        # Auto-verify channel/group join
        try:
            # Extract username from task_data
            chat_id = task_data.replace("https://t.me/", "").replace("@", "")
            if not chat_id.startswith("@"):
                chat_id = "@" + chat_id
            
            chat_member = bot.get_chat_member(chat_id, user_id)
            if chat_member.status in ['member', 'administrator', 'creator']:
                # Auto-verify
                cursor.execute("""
                    INSERT INTO user_tasks (user_id, task_id, verified, verified_at)
                    VALUES (?, ?, 1, ?)
                """, (user_id, task_id, datetime.now()))
                add_stars(user_id, reward, trigger_backup=True)
                conn.commit()
                
                user_name = get_user_display_name(user_id)
                
                bot.answer_callback_query(
                    call.id, 
                    f"✅ Task completed! You earned {reward} 🟡⭐!", 
                    show_alert=True
                )
                
                # Update message
                text = f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **TASK COMPLETED!** ✅
━━━━━━━━━━━━━━━━━━━━━

👤 **{user_name}**

📋 **Task:** {task_name}
💰 **Reward:** +{reward} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
📊 **New Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━
"""
                markup = InlineKeyboardMarkup()
                markup.row(
                    InlineKeyboardButton("📋 MORE TASKS 📋", callback_data="show_tasks"),
                    InlineKeyboardButton("💰 EARN MORE 💰", callback_data="earn")
                )
                
                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
                
                # Backup on task completion
                if GITHUB_TOKEN and GITHUB_REPO:
                    threading.Thread(target=backup_to_github, args=("task_complete", f"User {user_id} completed task {task_id}"), daemon=True).start()
                
            else:
                bot.answer_callback_query(
                    call.id, 
                    "❌ You haven't joined yet! Please join first.", 
                    show_alert=True
                )
        except Exception as e:
            print(f"❌ Task verification error: {e}")
            bot.answer_callback_query(
                call.id, 
                "❌ Error verifying. Please make sure you've joined and try again.", 
                show_alert=True
            )
    
    elif task_type in ["visit_link", "watch_video"]:
        # Manual verification needed
        cursor.execute("""
            INSERT INTO user_tasks (user_id, task_id, verified)
            VALUES (?, ?, 0)
        """, (user_id, task_id))
        conn.commit()
        
        user_name = get_user_display_name(user_id)
        
        # Notify admins
        for admin_id in ADMIN_IDS:
            try:
                admin_text = f"""
🔔 **TASK VERIFICATION NEEDED** 🔔

👤 **User:** {user_name}
🆔 **User ID:** `{user_id}`
📋 **Task:** {task_name}
💰 **Reward:** {reward} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
✅ To verify, use:
`/verify_task {user_id} {task_id}`
━━━━━━━━━━━━━━━━━━━━━
"""
                bot.send_message(admin_id, admin_text, parse_mode="Markdown")
            except:
                pass
        
        bot.answer_callback_query(
            call.id, 
            "✅ Task submitted for verification! Admin will verify soon.", 
            show_alert=True
        )
        
        # Update message
        text = f"""
━━━━━━━━━━━━━━━━━━━━━
⏳ **TASK SUBMITTED** ⏳
━━━━━━━━━━━━━━━━━━━━━

👤 **{user_name}**

📋 **Task:** {task_name}
💰 **Reward:** {reward} 🟡⭐

━━━━━━━━━━━━━━━━━━━━━
✅ Your task has been submitted for admin verification.

⏱️ Verification usually takes 5-15 minutes.

You will be notified when it's approved!
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📋 MORE TASKS 📋", callback_data="show_tasks"),
            InlineKeyboardButton("💰 EARN MORE 💰", callback_data="earn")
        )
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

@bot.message_handler(commands=['verify_task'])
def verify_task(message):
    """Admin command to verify task completion"""
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    
    try:
        parts = message.text.split()
        target_user = int(parts[1])
        task_id = int(parts[2])
        
        # Get task details
        cursor.execute("SELECT reward FROM tasks WHERE id=?", (task_id,))
        task = cursor.fetchone()
        if not task:
            bot.send_message(message.chat.id, "❌ Task not found!")
            return
        
        reward = task[0]
        
        # Update task verification
        cursor.execute("""
            UPDATE user_tasks 
            SET verified = 1, verified_by = ?, verified_at = ?
            WHERE user_id = ? AND task_id = ?
        """, (user_id, datetime.now(), target_user, task_id))
        
        # Add stars to user
        add_stars(target_user, reward, trigger_backup=True)
        conn.commit()
        
        # Notify user
        try:
            bot.send_message(
                target_user,
                f"✅ **Task Verified!**\n\n"
                f"Your task has been verified by an admin!\n"
                f"💰 You earned {reward} 🟡⭐!\n\n"
                f"📊 New Balance: {get_wallet(target_user)[1]} 🟡⭐",
                parse_mode="Markdown"
            )
        except:
            pass
        
        bot.send_message(message.chat.id, f"✅ Task verified for user {target_user}!")
        
        # Trigger backup
        if GITHUB_TOKEN and GITHUB_REPO:
            threading.Thread(target=backup_to_github, args=("task_verified", f"Admin verified task {task_id} for user {target_user}"), daemon=True).start()
        
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Usage: /verify_task [user_id] [task_id]")

# ================= REFERRAL LINK =================

@bot.callback_query_handler(func=lambda c: c.data == "refer")
def refer(call):
    user_id = call.from_user.id
    bot_name = bot.get_me().username
    refer_link = f"https://t.me/{bot_name}?start={user_id}"
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
📨 **REFER & EARN** 📨
━━━━━━━━━━━━━━━━━━━━━

👥 **Your Referrals:** {get_wallet(user_id)[3]}

━━━━━━━━━━━━━━━━━━━━━
💰 **Earn 5 🟡⭐ for every friend who joins!**
━━━━━━━━━━━━━━━━━━━━━

🔗 **Your referral link:**
`{refer_link}`

━━━━━━━━━━━━━━━━━━━━━
📤 **Share this link with your friends!**

💡 The more you refer, the more you earn!
━━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📋 COPY LINK 📋", callback_data=f"copy_{refer_link}"),
        InlineKeyboardButton("📊 LEADERBOARD 📊", callback_data="leaderboard")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= PREMIUM =================

@bot.callback_query_handler(func=lambda c: c.data == "premium")
def premium(call):
    user_id = call.from_user.id
    user = get_wallet(user_id)
    
    if user[4] == 1:
        text = """
━━━━━━━━━━━━━━━━━━━━━
💎 **PREMIUM ACTIVE** 💎
━━━━━━━━━━━━━━━━━━━━━

✅ You already have premium access!

━━━━━━━━━━━━━━━━━━━━━
**Your Premium Benefits:**
━━━━━━━━━━━━━━━━━━━━━
• 💳 Withdrawals enabled
• 💼 Admin withdrawal requests
• 🎯 Higher earning potential
• 👑 Priority support
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💳 WITHDRAW 💳", callback_data="withdraw_menu"),
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
    else:
        text = """
━━━━━━━━━━━━━━━━━━━━━
💎 **PREMIUM MEMBERSHIP** 💎
━━━━━━━━━━━━━━━━━━━━━

✨ **Unlock Exclusive Benefits:** ✨

━━━━━━━━━━━━━━━━━━━━━
✅ **Withdrawals Enabled**
   Convert your stars to rewards

✅ **Admin Withdrawal Requests**
   Request special withdrawals

✅ **Higher Earning Potential**
   More tasks, more rewards

✅ **Priority Support**
   Get help faster

━━━━━━━━━━━━━━━━━━━━━
💰 **Price:** Contact Admin
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📨 CONTACT ADMIN 📨", url="https://t.me/admin"),
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= BUY STARS =================

@bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
def buy_menu(call):
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
🟡 **BUY STARS** 🟡
━━━━━━━━━━━━━━━━━━━━━

💰 Purchase stars using Telegram Stars!

━━━━━━━━━━━━━━━━━━━━━
💫 **Available Packages:** 💫
━━━━━━━━━━━━━━━━━━━━━
"""

    for stars, price in STAR_PACKAGES.items():
        discount = 100 - int((price / int(stars)) * 100)
        text += f"• **{stars} Stars** - {price} ⭐️"
        if discount > 0:
            text += f" (Save {discount}%)\n"
        else:
            text += "\n"
    
    text += """
━━━━━━━━━━━━━━━━━━━━━
✅ Instant delivery to your wallet!
━━━━━━━━━━━━━━━━━━━━━
"""
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💫 BUY STARS 💫", callback_data="buy_show"))
    markup.row(
        InlineKeyboardButton("💰 EARN INSTEAD 💰", callback_data="earn"),
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
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
    markup.add(InlineKeyboardButton("🔙 BACK", callback_data="buy_menu"))
    
    bot.edit_message_text(
        "✨ **Choose a package:** ✨",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_buy(call):
    if call.data == "buy_menu":
        return
    
    stars = call.data.split("_")[1]
    price = STAR_PACKAGES[stars]
    
    prices = [LabeledPrice(label=f"{stars} Stars", amount=price)]
    
    bot.send_invoice(
        call.message.chat.id,
        title=f"⚡ Pulse Profit - {stars} Stars",
        description=f"Get {stars} 🟡⭐ stars for your Pulse Profit wallet!",
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
    
    add_stars(user_id, stars_purchased, trigger_backup=True)
    
    cursor.execute("""
        INSERT INTO payments (user_id, telegram_payment_charge_id, stars_purchased, amount_paid)
        VALUES (?, ?, ?, ?)
    """, (user_id, message.successful_payment.telegram_payment_charge_id, stars_purchased, amount_paid))
    conn.commit()
    
    user_name = get_user_display_name(user_id)
    
    bot.send_message(
        user_id,
        f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **PURCHASE SUCCESSFUL!** ✅
━━━━━━━━━━━━━━━━━━━━━

👤 **{user_name}**

✨ Added: **{stars_purchased}** 🟡⭐
💳 Payment ID: `{message.successful_payment.telegram_payment_charge_id}`

━━━━━━━━━━━━━━━━━━━━━
💰 **New Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━
""",
        parse_mode="Markdown"
    )
    
    # Notify admins
    for admin in ADMIN_IDS:
        try:
            bot.send_message(
                admin,
                f"💰 **New Purchase!**\n"
                f"User: {user_name} (`{user_id}`)\n"
                f"Stars: {stars_purchased} 🟡⭐\n"
                f"Paid: {amount_paid} ⭐️"
            )
        except:
            pass
    
    # Trigger backup
    if GITHUB_TOKEN and GITHUB_REPO:
        threading.Thread(target=backup_to_github, args=("purchase", f"User {user_id} purchased {stars_purchased} stars"), daemon=True).start()

# ================= BACK BUTTON =================

@bot.callback_query_handler(func=lambda c: c.data == "back")
def back(call):
    user_name = get_user_display_name(call.from_user.id)
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
⚡ **PULSE PROFIT** ⚡
━━━━━━━━━━━━━━━━━━━━━

👋 Welcome back **{user_name}**!

━━━━━━━━━━━━━━━━━━━━━
💰 **Your Balance:** {get_wallet(call.from_user.id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

👇 **Choose an option below:** 👇
"""
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ================= ADMIN DAILY BONUS =================

def daily_admin_bonus():
    """Give daily bonus to admins"""
    while True:
        time.sleep(86400)  # 24 hours
        reset_daily_withdrawals()
        for admin in ADMIN_IDS:
            cursor.execute("UPDATE users_wallet SET stars = stars + 100 WHERE user_id=?", (admin,))
        conn.commit()
        print("✅ Admin daily bonus added")
        
        # Backup after admin bonus
        if GITHUB_TOKEN and GITHUB_REPO:
            threading.Thread(target=backup_to_github, args=("admin_bonus", "Daily admin bonus added"), daemon=True).start()

threading.Thread(target=daily_admin_bonus, daemon=True).start()

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

# ================= NOOP CALLBACK =================

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call):
    """Do nothing callback"""
    bot.answer_callback_query(call.id)

# ================= MAIN EXECUTION =================

if __name__ == "__main__":
    print("=" * 50)
    print("⚡ PULSE PROFIT BOT ⚡")
    print("=" * 50)
    print("💰 Earning System: Active")
    print("👥 Referral System: Active")
    print("💳 Withdrawal System: Active")
    print("   - Admin Withdrawal: No premium required for admins")
    print("   - Stars Withdrawal: Available to all users")
    print("⭐ Telegram Stars: Active")
    print("📋 Task System: Active")
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
        port = int(os.environ.get('PORT', 10000))
        keep_alive = KeepAliveService(f"http://localhost:{port}/health")
        keep_alive.start()
    
    # Start Flask server
    port = int(os.environ.get('PORT', 10000))
    
    # Check if running under gunicorn
    if "gunicorn" in os.environ.get("SERVER_SOFTWARE", ""):
        print("✅ Running under gunicorn - Flask will be handled by gunicorn")
    else:
        print(f"🌐 Starting Flask server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False)
