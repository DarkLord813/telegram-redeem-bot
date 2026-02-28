import os
import random
import sqlite3
import requests
import threading
import time
import base64
import string
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

# ================= REQUIRED CHANNEL =================
REQUIRED_CHANNEL = "@PulseProfit012"
CHANNEL_LINK = "https://t.me/PulseProfit012"

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

# ================= REDEEM CODE SETTINGS =================
CODE_LENGTH = 8
CODE_CHARS = string.ascii_uppercase + string.digits  # A-Z, 0-9

# ================= DATABASE =================
conn = sqlite3.connect("pulse_profit.db", check_same_thread=False)
cursor = conn.cursor()

# Create tables
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_channel INTEGER DEFAULT 0,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    daily_withdrawn INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS referrals (
    referrer_id INTEGER,
    referred_id INTEGER UNIQUE,
    FOREIGN KEY (referrer_id) REFERENCES users(user_id),
    FOREIGN KEY (referred_id) REFERENCES users(user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS withdraw_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    withdrawal_type TEXT DEFAULT 'admin',
    status TEXT DEFAULT 'pending',
    request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    processed_time TIMESTAMP DEFAULT NULL,
    transaction_id TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_actions (
    user_id INTEGER,
    action_type TEXT,
    action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    telegram_payment_charge_id TEXT,
    stars_purchased INTEGER,
    amount_paid INTEGER,
    payment_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
""")

# Task system tables
cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name TEXT,
    task_type TEXT,
    task_data TEXT,
    reward INTEGER,
    max_completions INTEGER DEFAULT -1,
    completed_count INTEGER DEFAULT 0,
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
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
)
""")

# Redeem codes table
cursor.execute("""
CREATE TABLE IF NOT EXISTS redeem_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    amount INTEGER,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS redeemed_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id INTEGER,
    user_id INTEGER,
    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (code_id) REFERENCES redeem_codes(id),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
""")

# Admin sessions table
cursor.execute("""
CREATE TABLE IF NOT EXISTS admin_sessions (
    admin_id INTEGER PRIMARY KEY,
    session_data TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# Backup log table
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

# ================= REDEEM CODE FUNCTIONS =================

def generate_redeem_code():
    """Generate a unique redeem code"""
    while True:
        code = ''.join(random.choices(CODE_CHARS, k=CODE_LENGTH))
        # Add dashes for readability (e.g., ABC1-DEF2-GH3)
        formatted_code = '-'.join([code[i:i+4] for i in range(0, len(code), 4)])
        
        # Check if code already exists
        cursor.execute("SELECT id FROM redeem_codes WHERE code=?", (formatted_code,))
        if not cursor.fetchone():
            return formatted_code

def create_redeem_code(admin_id, amount, expires_days=30, max_uses=1):
    """Create a new redeem code"""
    code = generate_redeem_code()
    expires_at = datetime.now() + timedelta(days=expires_days)
    
    cursor.execute("""
        INSERT INTO redeem_codes (code, amount, created_by, expires_at, max_uses)
        VALUES (?, ?, ?, ?, ?)
    """, (code, amount, admin_id, expires_at, max_uses))
    conn.commit()
    
    return code

def redeem_code(user_id, code):
    """Redeem a code and add stars to user"""
    # Clean code (remove spaces, convert to uppercase)
    code = code.strip().upper()
    
    # Find the code
    cursor.execute("""
        SELECT id, amount, expires_at, max_uses, used_count, active 
        FROM redeem_codes WHERE code=?
    """, (code,))
    result = cursor.fetchone()
    
    if not result:
        return False, "❌ Invalid code!"
    
    code_id, amount, expires_at, max_uses, used_count, active = result
    
    # Check if active
    if not active:
        return False, "❌ This code has been deactivated!"
    
    # Check if expired
    expires = datetime.fromisoformat(expires_at)
    if datetime.now() > expires:
        return False, "❌ This code has expired!"
    
    # Check if max uses reached
    if used_count >= max_uses:
        return False, "❌ This code has already been used!"
    
    # Check if user already used this code (for single-use codes)
    cursor.execute("SELECT id FROM redeemed_codes WHERE code_id=? AND user_id=?", (code_id, user_id))
    if cursor.fetchone():
        return False, "❌ You have already redeemed this code!"
    
    # Add stars to user
    add_stars(user_id, amount, trigger_backup=True)
    
    # Update code usage
    cursor.execute("""
        UPDATE redeem_codes SET used_count = used_count + 1 WHERE id=?
    """, (code_id,))
    
    # Record redemption
    cursor.execute("""
        INSERT INTO redeemed_codes (code_id, user_id) VALUES (?, ?)
    """, (code_id, user_id))
    conn.commit()
    
    return True, f"✅ Success! You received {amount} 🟡⭐!"

def get_redeem_codes(admin_id=None, limit=20):
    """Get redeem codes, optionally filtered by admin"""
    if admin_id:
        cursor.execute("""
            SELECT id, code, amount, created_at, expires_at, max_uses, used_count, active
            FROM redeem_codes WHERE created_by=?
            ORDER BY created_at DESC LIMIT ?
        """, (admin_id, limit))
    else:
        cursor.execute("""
            SELECT id, code, amount, created_at, expires_at, max_uses, used_count, active
            FROM redeem_codes ORDER BY created_at DESC LIMIT ?
        """, (limit,))
    
    return cursor.fetchall()

def deactivate_redeem_code(code_id):
    """Deactivate a redeem code"""
    cursor.execute("UPDATE redeem_codes SET active=0 WHERE id=?", (code_id,))
    conn.commit()
    return cursor.rowcount > 0

# ================= CHANNEL VERIFICATION FUNCTIONS =================

def check_channel_membership(user_id):
    """Check if user has joined the required channel"""
    try:
        member = bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"❌ Channel check error: {e}")
        return False

def mark_user_joined(user_id, username, first_name):
    """Mark user as having joined the channel"""
    cursor.execute("""
        INSERT OR REPLACE INTO users (user_id, username, first_name, joined_channel)
        VALUES (?, ?, ?, 1)
    """, (user_id, username, first_name))
    conn.commit()

def is_user_joined(user_id):
    """Check if user has joined the channel in database"""
    cursor.execute("SELECT joined_channel FROM users WHERE user_id=?", (user_id,))
    result = cursor.fetchone()
    if result:
        return result[0] == 1
    return False

# ================= CHANNEL VERIFICATION MIDDLEWARE =================

def check_channel_and_respond(user_id, chat_id, message_id=None):
    """Check channel membership and respond with join message if needed"""
    # Check if user is admin (admins bypass channel requirement)
    if is_admin(user_id):
        return True
    
    # Check database first
    if is_user_joined(user_id):
        return True
    
    # Check actual membership
    if check_channel_membership(user_id):
        # Get user info
        try:
            user_info = bot.get_chat_member(user_id, user_id).user
            username = user_info.username if user_info.username else ""
            first_name = user_info.first_name
        except:
            username = ""
            first_name = "User"
        
        # Mark as joined
        mark_user_joined(user_id, username, first_name)
        return True
    
    # User hasn't joined - show join message
    try:
        user_info = bot.get_chat_member(user_id, user_id).user
        first_name = user_info.first_name
    except:
        first_name = "User"
    
    channel_text = f"""
━━━━━━━━━━━━━━━━━━━━━
🔒 **CHANNEL REQUIRED** 🔒
━━━━━━━━━━━━━━━━━━━━━

👋 Hello **{first_name}**!

To access Pulse Profit bot, you must join our channel first.

━━━━━━━━━━━━━━━━━━━━━
📢 **Channel:** {REQUIRED_CHANNEL}
🔗 **Link:** {CHANNEL_LINK}
━━━━━━━━━━━━━━━━━━━━━

✅ **Steps to access:**
1. Click the button below to join
2. Return to the bot
3. Click "I've Joined ✓"

━━━━━━━━━━━━━━━━━━━━━
"""
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=CHANNEL_LINK),
        InlineKeyboardButton("✅ I'VE JOINED ✅", callback_data="verify_channel")
    )
    
    if message_id:
        bot.edit_message_text(
            channel_text,
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        bot.send_message(
            chat_id,
            channel_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    return False

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

# ================= AUTO WITHDRAWAL PROCESSOR (TELEGRAM STARS) =================

def process_withdrawals():
    """Automatically process pending withdrawals and send Telegram Stars"""
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
                    # Send Telegram Stars directly to user
                    try:
                        # Create invoice to send stars to user's Telegram Stars balance
                        prices = [LabeledPrice(label=f"Withdrawal of {amount} Stars", amount=amount)]
                        
                        # Send the stars directly to user's Telegram wallet
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
                                f"Check your Telegram Stars balance - they've been added directly to your account!",
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

# ================= CHANNEL VERIFICATION HANDLER =================

@bot.callback_query_handler(func=lambda c: c.data == "verify_channel")
def verify_channel(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if check_channel_membership(user_id):
        # Mark as joined in database
        try:
            user_info = bot.get_chat_member(user_id, user_id).user
            username = user_info.username if user_info.username else ""
            first_name = user_info.first_name
        except:
            username = ""
            first_name = "User"
        
        mark_user_joined(user_id, username, first_name)
        
        # Get wallet
        get_wallet(user_id)
        
        bot.answer_callback_query(call.id, "✅ Verification successful! Access granted.", show_alert=True)
        
        # Show main menu
        welcome_text = f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **VERIFICATION SUCCESSFUL!** ✅
━━━━━━━━━━━━━━━━━━━━━

👋 Welcome to **Pulse Profit**!

━━━━━━━━━━━━━━━━━━━━━
💰 **Your Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

👇 **Choose an option below:** 👇
"""
        
        markup = main_menu(user_id)
        
        bot.edit_message_text(
            welcome_text,
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        bot.answer_callback_query(
            call.id, 
            "❌ You haven't joined the channel yet! Please join first.", 
            show_alert=True
        )

# ================= MAIN MENU =================

def main_menu(user_id):
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
    markup.row(
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    
    # Add admin panel button for admins
    if is_admin(user_id):
        markup.row(
            InlineKeyboardButton("👑 ADMIN PANEL 👑", callback_data="admin_panel")
        )
    
    return markup

# ================= REDEEM CODE MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "redeem_menu")
def redeem_menu(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    user_name = get_user_display_name(user_id)
    
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
🎫 **REDEEM CODE** 🎫
━━━━━━━━━━━━━━━━━━━━━

👋 **{user_name}**

Have a promo code? Enter it below to receive free stars!

━━━━━━━━━━━━━━━━━━━━━
📝 **How to redeem:**
1. Type your code exactly as given
2. Code format: XXXX-XXXX-XX
3. Stars will be added instantly
4. Each code can only be used once

━━━━━━━━━━━━━━━━━━━━━
💡 Example: `ABC1-DEF2-GH3`
━━━━━━━━━━━━━━━━━━━━━

👇 **Send your code now:**
"""
    
    # Store that we're waiting for a redeem code
    cursor.execute("""
        INSERT OR REPLACE INTO user_actions (user_id, action_type, action_time)
        VALUES (?, ?, ?)
    """, (user_id, "awaiting_redeem_code", datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔙 BACK TO MENU 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= START COMMAND =================

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    username = message.from_user.username if message.from_user.username else ""
    first_name = message.from_user.first_name
    
    # Check if user already in database
    cursor.execute("SELECT joined_channel FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    
    # Check if user has joined channel
    has_joined = False
    if user and user[0] == 1:
        has_joined = True
    elif check_channel_membership(user_id):
        mark_user_joined(user_id, username, first_name)
        has_joined = True
    
    if has_joined:
        # User verified - get wallet and show main menu
        get_wallet(user_id)
        
        # Check for referral
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
        
        welcome_text = f"""
━━━━━━━━━━━━━━━━━━━━━
⚡ **WELCOME TO PULSE PROFIT** ⚡
━━━━━━━━━━━━━━━━━━━━━

👋 Hello **{get_user_display_name(user_id)}**!

━━━━━━━━━━━━━━━━━━━━━
💰 **Your Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

👇 **Choose an option below:** 👇
"""
        
        markup = main_menu(user_id)
        
        bot.send_message(
            chat_id,
            welcome_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        # User needs to join channel
        channel_text = f"""
━━━━━━━━━━━━━━━━━━━━━
🔒 **CHANNEL REQUIRED** 🔒
━━━━━━━━━━━━━━━━━━━━━

👋 Hello **{first_name}**!

To access Pulse Profit bot, you must join our channel first.

━━━━━━━━━━━━━━━━━━━━━
📢 **Channel:** {REQUIRED_CHANNEL}
🔗 **Link:** {CHANNEL_LINK}
━━━━━━━━━━━━━━━━━━━━━

✅ **Steps to access:**
1. Click the button below to join
2. Return to the bot
3. Click "I've Joined ✓"

━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=CHANNEL_LINK),
            InlineKeyboardButton("✅ I'VE JOINED ✅", callback_data="verify_channel")
        )
        
        bot.send_message(
            chat_id,
            channel_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )

# ================= EARN STARS =================

@bot.callback_query_handler(func=lambda c: c.data == "earn")
def earn(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
        SET stars = stars + ?, total_earned = total_earned + ?
        WHERE user_id=?
    """, (reward, reward, user_id))
    conn.commit()
    
    # Update tasks_done
    cursor.execute("UPDATE users_wallet SET tasks_done = tasks_done + 1 WHERE user_id=?", (user_id,))
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
    
    markup = main_menu(user_id)
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= PROFILE =================

@bot.callback_query_handler(func=lambda c: c.data == "profile")
def profile(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    user = get_wallet(user_id)
    user_name = get_user_display_name(user_id)
    
    # Get rank (excluding admins)
    placeholders = ','.join('?' * len(ADMIN_IDS))
    cursor.execute(f"""
        SELECT COUNT(*) + 1 FROM users_wallet 
        WHERE stars > (SELECT stars FROM users_wallet WHERE user_id = ?)
        AND user_id NOT IN ({placeholders})
    """, (user_id, *ADMIN_IDS))
    rank = cursor.fetchone()[0]
    
    # Calculate progress to next rank (excluding admins)
    cursor.execute(f"""
        SELECT stars FROM users_wallet 
        WHERE stars > (SELECT stars FROM users_wallet WHERE user_id = ?)
        AND user_id NOT IN ({placeholders})
        ORDER BY stars ASC LIMIT 1
    """, (user_id, *ADMIN_IDS))
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
    
    markup = main_menu(user_id)
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= LEADERBOARD WITH NAMES (ADMINS REMOVED) =================

@bot.callback_query_handler(func=lambda c: c.data == "leaderboard")
def leaderboard(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    # Get top 10 users by stars (excluding admins)
    placeholders = ','.join('?' * len(ADMIN_IDS))
    cursor.execute(f"""
        SELECT user_id, stars, total_earned, referrals 
        FROM users_wallet 
        WHERE user_id NOT IN ({placeholders})
        ORDER BY stars DESC 
        LIMIT 10
    """, ADMIN_IDS)
    top_users = cursor.fetchall()
    
    # Create colorful leaderboard text
    text = """
━━━━━━━━━━━━━━━━━━━━━
⚡ **PULSE PROFIT LEADERBOARD** ⚡
━━━━━━━━━━━━━━━━━━━━━

"""
    
    # Medal emojis for top 3
    medals = ["🥇 **GOLD**", "🥈 **SILVER**", "🥉 **BRONZE**"]
    
    text += "🌟 **TOP EARNERS** 🌟\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not top_users:
        text += "No users yet! Be the first to earn stars!\n\n"
    else:
        # Process top users
        rank = 1
        for user_id_top, stars, total_earned, referrals in top_users:
            user_name = get_user_display_name(user_id_top)
            
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
    
    # Add total stats (excluding admins)
    cursor.execute(f"SELECT COUNT(*) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    total_users = cursor.fetchone()[0]
    
    cursor.execute(f"SELECT SUM(stars) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    total_stars = cursor.fetchone()[0] or 0
    
    cursor.execute(f"SELECT AVG(stars) FROM users_wallet WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    avg_stars = int(cursor.fetchone()[0] or 0)
    
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "📊 **STATISTICS** 📊\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"👥 **Total Users:** `{total_users:,}`\n"
    text += f"💰 **Total Stars:** `{total_stars:,}` 🟡\n"
    text += f"📈 **Average Stars:** `{avg_stars:,}` 🟡\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += "💡 _Keep earning to reach the top!_"
    
    markup = main_menu(user_id)
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= TASKS SYSTEM =================

@bot.callback_query_handler(func=lambda c: c.data == "show_tasks")
def show_tasks(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        markup.row(
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
        
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
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
        task_id, task_name, task_type, task_data, reward, max_comp, completed, active, created_by, created_at = task
        
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("task_details_"))
def task_details(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    task_id = int(call.data.replace("task_details_", ""))
    
    cursor.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        bot.answer_callback_query(call.id, "❌ Task not found!", show_alert=True)
        return
    
    task_id, task_name, task_type, task_data, reward, max_comp, completed, active, created_by, created_at = task
    
    # Check if user already completed this task
    cursor.execute("SELECT * FROM user_tasks WHERE user_id=? AND task_id=?", (user_id, task_id))
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("claim_task_"))
def claim_task(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            chat_id_to_check = task_data.replace("https://t.me/", "").replace("@", "")
            if not chat_id_to_check.startswith("@"):
                chat_id_to_check = "@" + chat_id_to_check
            
            chat_member = bot.get_chat_member(chat_id_to_check, user_id)
            if chat_member.status in ['member', 'administrator', 'creator']:
                # Auto-verify
                cursor.execute("""
                    INSERT INTO user_tasks (user_id, task_id, verified, verified_at)
                    VALUES (?, ?, 1, ?)
                """, (user_id, task_id, datetime.now()))
                add_stars(user_id, reward, trigger_backup=True)
                conn.commit()
                
                # Update task completed count
                cursor.execute("UPDATE tasks SET completed_count = completed_count + 1 WHERE id=?", (task_id,))
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
                markup.row(
                    InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
                )
                
                bot.edit_message_text(
                    text,
                    chat_id,
                    message_id,
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
        markup.row(
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

# ================= REFERRAL LINK =================

@bot.callback_query_handler(func=lambda c: c.data == "refer")
def refer(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= COPY LINK HANDLER =================

@bot.callback_query_handler(func=lambda c: c.data.startswith("copy_"))
def copy_link(call):
    link = call.data.replace("copy_", "")
    bot.answer_callback_query(call.id, f"✅ Link copied! Share it with friends: {link}", show_alert=True)

# ================= PREMIUM =================

@bot.callback_query_handler(func=lambda c: c.data == "premium")
def premium(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    user = get_wallet(user_id)
    user_name = get_user_display_name(user_id)
    
    if user[4] == 1:
        text = f"""
━━━━━━━━━━━━━━━━━━━━━
💎 **PREMIUM ACTIVE** 💎
━━━━━━━━━━━━━━━━━━━━━

✅ You already have premium access, **{user_name}**!

━━━━━━━━━━━━━━━━━━━━━
**Your Premium Benefits:**
━━━━━━━━━━━━━━━━━━━━━
• 💳 **Withdrawals enabled**
• 💼 **Admin withdrawal requests**
• 🎯 **Higher earning potential**
• 👑 **Priority support**
━━━━━━━━━━━━━━━━━━━━━
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💳 WITHDRAW 💳", callback_data="withdraw_menu"),
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        markup.row(
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
    else:
        text = f"""
━━━━━━━━━━━━━━━━━━━━━
💎 **PREMIUM MEMBERSHIP** 💎
━━━━━━━━━━━━━━━━━━━━━

✨ **Unlock Exclusive Benefits,** **{user_name}**! ✨

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
💰 **Get Premium Now!**
━━━━━━━━━━━━━━━━━━━━━

👇 **Click the button below to get premium access:**
"""
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("💎 GET PREMIUM 💎", url="https://t.me/MA5T3RBot"),
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        markup.row(
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
        )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= BUY STARS =================

@bot.callback_query_handler(func=lambda c: c.data == "buy_menu")
def buy_menu(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data == "buy_show")
def buy_show(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    markup = InlineKeyboardMarkup()
    
    for stars, price in STAR_PACKAGES.items():
        markup.add(InlineKeyboardButton(
            f"💫 {stars} Stars - {price} ⭐️", 
            callback_data=f"buy_{stars}"
        ))
    
    markup.add(InlineKeyboardButton("🔙 BACK", callback_data="buy_menu"))
    
    bot.edit_message_text(
        "✨ **Choose a package:** ✨",
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def process_buy(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    if call.data == "buy_menu":
        return
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, call.message.message_id):
        return
    
    stars = call.data.split("_")[1]
    price = STAR_PACKAGES[stars]
    
    prices = [LabeledPrice(label=f"{stars} Stars", amount=price)]
    
    bot.send_invoice(
        chat_id,
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

# ================= WITHDRAWAL MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_menu")
def withdraw_menu(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            InlineKeyboardButton("💼 ADMIN WITHDRAW 💼", callback_data="withdraw_admin")
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK TO MENU 🔙", callback_data="back")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= STARS WITHDRAWAL MENU =================

@bot.callback_query_handler(func=lambda c: c.data == "withdraw_stars_menu")
def withdraw_stars_menu(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        markup.row(
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
        )
        
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("withdraw_stars_"))
def withdraw_stars_amount(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            chat_id,
            message_id,
            parse_mode="Markdown"
        )
        return
    
    amount = int(call.data.replace("withdraw_stars_", ""))
    process_stars_withdrawal(chat_id, message_id, user_id, amount, call)

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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
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
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
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
            InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
        )
        markup.row(
            InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
        )
        
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
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
        InlineKeyboardButton("🎫 REDEEM CODE 🎫", callback_data="redeem_menu")
    )
    markup.row(
        InlineKeyboardButton("🔙 BACK 🔙", callback_data="withdraw_menu")
    )
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= BACK BUTTON =================

@bot.callback_query_handler(func=lambda c: c.data == "back")
def back(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    # Check channel membership
    if not check_channel_and_respond(user_id, chat_id, message_id):
        return
    
    user_name = get_user_display_name(user_id)
    text = f"""
━━━━━━━━━━━━━━━━━━━━━
⚡ **PULSE PROFIT** ⚡
━━━━━━━━━━━━━━━━━━━━━

👋 Welcome back **{user_name}**!

━━━━━━━━━━━━━━━━━━━━━
💰 **Your Balance:** {get_wallet(user_id)[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━

👇 **Choose an option below:** 👇
"""
    
    markup = main_menu(user_id)
    
    bot.edit_message_text(
        text,
        chat_id,
        message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

# ================= NOOP CALLBACK =================

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def noop(call):
    """Do nothing callback"""
    bot.answer_callback_query(call.id)

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

# ================= HANDLE ALL TEXT MESSAGES =================

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle all text messages including withdrawals and redeem codes"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()
    
    print(f"📨 Message from {user_id}: {text[:50]}...")  # Debug log
    
    # First, check if this is for redeem code
    cursor.execute("""
        SELECT action_type FROM user_actions 
        WHERE user_id = ? AND action_type = 'awaiting_redeem_code'
        ORDER BY action_time DESC LIMIT 1
    """, (user_id,))
    
    redeem_session = cursor.fetchone()
    if redeem_session:
        # Process redeem code
        success, result_message = redeem_code(user_id, text)
        
        # Clear the waiting state
        cursor.execute("DELETE FROM user_actions WHERE user_id=? AND action_type=?", (user_id, "awaiting_redeem_code"))
        conn.commit()
        
        if success:
            # Get updated balance
            user = get_wallet(user_id)
            user_name = get_user_display_name(user_id)
            
            response = f"""
━━━━━━━━━━━━━━━━━━━━━
✅ **CODE REDEEMED!** ✅
━━━━━━━━━━━━━━━━━━━━━

👤 **{user_name}**

{result_message}

━━━━━━━━━━━━━━━━━━━━━
💰 **New Balance:** {user[1]} 🟡⭐
━━━━━━━━━━━━━━━━━━━━━
"""
        else:
            response = f"""
━━━━━━━━━━━━━━━━━━━━━
❌ **REDEMPTION FAILED** ❌
━━━━━━━━━━━━━━━━━━━━━

{result_message}

━━━━━━━━━━━━━━━━━━━━━
"""
        
        markup = main_menu(user_id)
        
        bot.send_message(
            chat_id,
            response,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return True
    
    # Check if this is for withdrawal amount
    cursor.execute("""
        SELECT action_type FROM user_actions 
        WHERE user_id = ? AND action_type IN ('awaiting_stars_withdraw', 'awaiting_admin_withdraw')
        ORDER BY action_time DESC LIMIT 1
    """, (user_id,))
    
    result = cursor.fetchone()
    if result:
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
    
    return False

# ================= ADMIN COMMANDS =================

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
        
        # Update task completed count
        cursor.execute("UPDATE tasks SET completed_count = completed_count + 1 WHERE id=?", (task_id,))
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
    print("🎫 Redeem Code System: Active")
    print("👑 Admin Panel: Active")
    print("🏆 Leaderboard: Admins hidden from view")
    print("🔒 Force Join: Required (@PulseProfit012)")
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
