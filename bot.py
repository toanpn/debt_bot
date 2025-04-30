import sqlite3
from telegram.ext import Updater, CommandHandler, Filters, MessageHandler
from telegram import MessageEntity
from datetime import datetime
import math
import requests
from io import BytesIO
import os
import sys
import atexit
import time
import threading
import shutil
import google.generativeai as genai
import re
import json
from openai import OpenAI  # Add import for OpenAI SDK

# ====== Model Configuration ======
# Wrapper class for different LLM models
class LLMWrapper:
    def __init__(self, model_name=None):
        # Default to gemini if no model specified
        self.model_name = model_name if model_name else 'gemini'
        
        # Initialize the selected model
        if self.model_name == 'gemini':
            # Set up Gemini API
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            print(f"Using Gemini model: gemini-2.0-flash")
        elif self.model_name == 'grok':
            # Set up Grok API
            self.grok_api_key = os.getenv("GROK_API_KEY")
            self.grok_api_url = "https://api.x.ai/v1/chat/completions"
            self.model_id = "grok-3-mini-beta"
            print(f"Using Grok model: {self.model_id}")
        elif self.model_name == 'deepseek':
            # Set up DeepSeek API using OpenAI SDK
            self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
            self.model_id = "deepseek-chat"  # Using DeepSeek-V3 model
            self.client = OpenAI(
                api_key=self.deepseek_api_key,
                base_url="https://api.deepseek.com"
            )
            print(f"Using DeepSeek model: {self.model_id}")
        else:
            # Default to Gemini if unknown model specified
            print(f"Unknown model '{self.model_name}', defaulting to Gemini")
            self.model_name = 'gemini'
            genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
            self.model = genai.GenerativeModel('gemini-2.0-flash')
    
    def generate_content(self, prompt):
        """Generate content from the selected model with a common interface"""
        if self.model_name == 'gemini':
            # Use Gemini's native API
            return self.model.generate_content(prompt)
        elif self.model_name == 'grok':
            # Use Grok API via requests
            try:
                headers = {
                    "Authorization": f"Bearer {self.grok_api_key}",
                    "Content-Type": "application/json"
                }
                
                data = {
                    "model": self.model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
                
                response = requests.post(
                    self.grok_api_url, 
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    # Create a response object similar to Gemini's for consistency
                    class GrokResponse:
                        def __init__(self, text):
                            self.text = text
                    
                    return GrokResponse(result['choices'][0]['message']['content'])
                else:
                    print(f"Grok API error: {response.status_code}, {response.text}")
                    return None
            except Exception as e:
                print(f"Error calling Grok API: {e}")
                return None
        elif self.model_name == 'deepseek':
            # Use DeepSeek API via OpenAI SDK
            try:
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    stream=False
                )
                
                # Create a response object similar to Gemini's for consistency
                class DeepSeekResponse:
                    def __init__(self, text):
                        self.text = text
                
                return DeepSeekResponse(response.choices[0].message.content)
            except Exception as e:
                print(f"Error calling DeepSeek API: {e}")
                return None
        
        # Fallback for unknown models
        return None

# Initialize the LLM wrapper
llm = LLMWrapper()

# Set database path - use environment variable or default to data directory
DB_DIR = os.environ.get('DB_DIR', os.path.join(os.path.expanduser('~'), 'bot_data'))
DB_PATH = os.path.join(DB_DIR, 'debtbot.db')

# Define your admin user IDs here - moved up for backup functions to use
ADMIN_IDS = [1095200180]  # Replace with your Telegram user ID

# Define repository directory where backup files might be stored
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DB_PATH = os.path.join(REPO_DIR, 'debtbot.db')

# Check for DB file in repository first
def check_repository_db():
    if os.path.exists(REPO_DB_PATH) and os.path.isfile(REPO_DB_PATH):
        print(f"Found database file in repository: {REPO_DB_PATH}")
        # Ensure DB_DIR exists
        os.makedirs(DB_DIR, exist_ok=True)
        # Copy the repo database file to the standard location
        shutil.copy2(REPO_DB_PATH, DB_PATH)
        print(f"Copied database from repository to: {DB_PATH}")
        return True
    return False

# Instance check to prevent multiple bots
def check_instance():
    pid_file = os.path.join(DB_DIR, 'debtbot.pid')
    
    # Check if PID file exists
    if os.path.isfile(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        
        # Check if process with this PID is running
        try:
            # Try to check if process exists (works on Unix-like systems)
            os.kill(int(old_pid), 0)
            print(f"Bot is already running with PID {old_pid}!")
            print("If you're sure no other instance is running, delete the PID file:")
            print(f"rm {pid_file}")
            sys.exit(1)
        except (OSError, ValueError):
            # Process not running, we can continue
            pass
    
    # Write our PID
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    # Register cleanup function to remove PID file on exit
    def cleanup():
        try:
            os.remove(pid_file)
        except:
            pass
    
    atexit.register(cleanup)

# Ensure directories exist
os.makedirs(DB_DIR, exist_ok=True)
print(f"Using database at: {DB_PATH}")

# Check if database exists in repository first
check_repository_db()

# Check if another instance is running
check_instance()

# ====== DB Migration ======

def column_exists(cursor, table_name, column_name):
    """Check if a column exists in a table."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [column[1] for column in cursor.fetchall()]
    return column_name in columns

def migrate_database():
    print("Checking database schema...")
    conn_migrate = sqlite3.connect(DB_PATH)
    cursor_migrate = conn_migrate.cursor()
    
    try:
        # Ensure debts table exists
        cursor_migrate.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creditor TEXT,
            debtor TEXT,
            amount REAL,
            note TEXT,
            timestamp TEXT,
            chat_id INTEGER DEFAULT 0
        )
        """)
        
        # Check if chat_id column exists in debts
        if not column_exists(cursor_migrate, 'debts', 'chat_id'):
            print("Adding chat_id column to debts table...")
            cursor_migrate.execute("ALTER TABLE debts ADD COLUMN chat_id INTEGER DEFAULT 0")
            print("Added chat_id column to debts.")
        else:
            print("debts table schema is up-to-date.")

        # Ensure name_mappings table exists
        cursor_migrate.execute("""
        CREATE TABLE IF NOT EXISTS name_mappings (
            username TEXT,
            display_name TEXT,
            chat_id INTEGER DEFAULT 0,
            PRIMARY KEY (username, chat_id)
        )
        """)
        
        # Check if chat_id column exists in name_mappings
        if not column_exists(cursor_migrate, 'name_mappings', 'chat_id'):
            print("Adding chat_id column to name_mappings table...")
            # Need to handle potential primary key conflicts - easiest is usually recreate
            print("Recreating name_mappings table for schema update...")
            cursor_migrate.execute("CREATE TABLE name_mappings_temp AS SELECT username, display_name, 0 as chat_id FROM name_mappings")
            cursor_migrate.execute("DROP TABLE name_mappings")
            cursor_migrate.execute("""
            CREATE TABLE name_mappings (
                username TEXT,
                display_name TEXT,
                chat_id INTEGER DEFAULT 0,
                PRIMARY KEY (username, chat_id)
            )
            """)
            cursor_migrate.execute("INSERT INTO name_mappings (username, display_name, chat_id) SELECT username, display_name, chat_id FROM name_mappings_temp")
            cursor_migrate.execute("DROP TABLE name_mappings_temp")
            print("Recreated name_mappings table with chat_id.")
        else:
            print("name_mappings table schema is up-to-date.")

        # Ensure qr_codes table exists
        cursor_migrate.execute("""
        CREATE TABLE IF NOT EXISTS qr_codes (
            username TEXT,
            image_url TEXT,
            chat_id INTEGER,
            PRIMARY KEY (username, chat_id)
        )
        """)
        print("qr_codes table schema checked.")
        
        conn_migrate.commit()
        print("Database schema check completed.")
        
    except sqlite3.Error as e:
        print(f"Database migration error: {e}")
    finally:
        conn_migrate.close()

# Run migration check
migrate_database()

# ====== DB Setup ======
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# These CREATE TABLE IF NOT EXISTS calls are now somewhat redundant 
# because migrate_database ensures they exist, but leaving them is harmless.
cursor.execute("""
CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor TEXT,
    debtor TEXT,
    amount REAL,
    note TEXT,
    timestamp TEXT,
    chat_id INTEGER
)
""")

# Create name mapping table
cursor.execute("""
CREATE TABLE IF NOT EXISTS name_mappings (
    username TEXT,
    display_name TEXT,
    chat_id INTEGER,
    PRIMARY KEY (username, chat_id)
)
""")
conn.commit()

# ====== Helper Functions ======

def get_display_name(username, chat_id):
    cursor.execute("SELECT display_name FROM name_mappings WHERE username = ? AND chat_id = ?", (username, chat_id))
    result = cursor.fetchone()
    return result[0] if result else f"@{username}"

# ====== Command Handlers ======

def add_debt(update, context):
    try:
        chat_id = update.effective_chat.id
        creditor = update.message.from_user.username
        
        if not creditor:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram để sử dụng bot này.")
            return
        
        if len(context.args) < 2:
            update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /adddebt 500 @toan [Trà sữa]")
            return
            
        # Parse amount (first argument)
        try:
            amount = float(context.args[0])
        except ValueError:
            update.message.reply_text("❌ Số tiền Khôm hợp lệ. Ví dụ: /adddebt 500 @toan [Trà sữa]")
            return
        
        # Extract debtors (all args starting with @)
        debtors = []
        for arg in context.args[1:]:
            if arg.startswith('@'):
                debtors.append(arg.replace('@', ''))
        
        if not debtors:
            update.message.reply_text("❌ Vui lòng chỉ định ít nhất một người nhé. Ví dụ: /adddebt 500 @toan [Trà sữa]")
            return
            
        # Extract note (anything after usernames)
        note_start_idx = 1 + len(debtors)
        for i in range(1, len(context.args)):
            if not context.args[i].startswith('@'):
                note_start_idx = i
                break
                
        note = " ".join(context.args[note_start_idx:]) if note_start_idx < len(context.args) else ""
            
        timestamp = datetime.now().isoformat()
        
        results = []
        for debtor in debtors:
            if creditor.lower() == debtor.lower():
                results.append(f"❌ Khôm thể ghi nợ chính mình ({get_display_name(debtor, chat_id)}).")
                continue
                
            cursor.execute(
                "INSERT INTO debts (creditor, debtor, amount, note, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
                (creditor, debtor, amount, note, timestamp, chat_id)
            )
            
            debtor_display = get_display_name(debtor, chat_id)
            results.append(f"✅ Đã ghi nợ {debtor_display} {format_money(amount)}" + (f" cho {note}" if note else ""))
        
        conn.commit()
        
        # Compose response message
        if len(results) == 1:
            update.message.reply_text(results[0])
        else:
            creditor_display = get_display_name(creditor, chat_id)
            msg = f"🧾 KẾT QUẢ GHI NỢ TỪ {creditor_display}:\n\n"
            for result in results:
                msg += f"{result}\n"
            update.message.reply_text(msg)
            
    except IndexError:
        update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /adddebt 500 @toan [Trà sữa]")
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def summary(update, context):
    chat_id = update.effective_chat.id
    
    # If username is provided as arg, show summary for that user
    if context.args:
        username = context.args[0].replace('@', '')
    else:
        username = update.message.from_user.username
        if not username:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram hoặc chỉ định username. Ví dụ: /summary @toan")
            return
    
    cursor.execute(
        "SELECT debtor, SUM(amount) FROM debts WHERE creditor = ? AND chat_id = ? GROUP BY debtor", 
        (username, chat_id)
    )
    you_own = cursor.fetchall()

    cursor.execute(
        "SELECT creditor, SUM(amount) FROM debts WHERE debtor = ? AND chat_id = ? GROUP BY creditor", 
        (username, chat_id)
    )
    you_owe = cursor.fetchall()

    display_name = get_display_name(username, chat_id)
    msg = f"📊 TỔNG HỢP NỢ CHO {display_name}\n\n"
    
    # Better visualization with emoji bars
    if you_own:
        msg += "🟢 NGƯỜI KHÁC NỢ:\n"
        # Sort by amount in descending order
        you_own.sort(key=lambda x: x[1], reverse=True)
        max_own = max([amt for _, amt in you_own]) if you_own else 0
        max_name_length = max([len(get_display_name(debtor, chat_id)) for debtor, _ in you_own])
        
        for debtor, amt in you_own:
            debtor_display = get_display_name(debtor, chat_id)
            bar_count = int(10 * amt / max_own) if max_own > 0 else 0
            bar = "█" * bar_count
            # Format with aligned columns
            msg += f" - {debtor_display.ljust(max_name_length)} : {format_money(amt)} {bar}\n"
    else:
        msg += "🟢 NGƯỜI KHÁC NỢ: Khôm có\n"

    if you_owe:
        msg += "\n🔴 NỢ NGƯỜI KHÁC:\n"
        # Sort by amount in descending order
        you_owe.sort(key=lambda x: x[1], reverse=True)
        max_owe = max([amt for _, amt in you_owe]) if you_owe else 0
        max_name_length = max([len(get_display_name(creditor, chat_id)) for creditor, _ in you_owe])
        
        for creditor, amt in you_owe:
            creditor_display = get_display_name(creditor, chat_id)
            bar_count = int(10 * amt / max_owe) if max_owe > 0 else 0
            bar = "█" * bar_count
            # Format with aligned columns
            msg += f" - {creditor_display.ljust(max_name_length)} : {format_money(amt)} {bar}\n"
    else:
        msg += "\n🔴 NỢ NGƯỜI KHÁC: Khôm có\n"
        
    # Calculate net balance
    total_owned = sum([amt for _, amt in you_own]) if you_own else 0
    total_owe = sum([amt for _, amt in you_owe]) if you_owe else 0
    net_balance = total_owned - total_owe
    
    msg += f"\n💰 CÂN BẰNG: {format_money(net_balance)}"
    if net_balance > 0:
        msg += " (Bạn được nhận thêm)"
    elif net_balance < 0:
        msg += " (Bạn cần trả thêm)"
    else:
        msg += " (Hòa vốn)"

    update.message.reply_text(msg)

# Helper function for formatting money with commas
def format_money(amount):
    return f"{amount:,.0f} đ"

def clear_debt(update, context):
    try:
        chat_id = update.effective_chat.id
        user = update.message.from_user.username
        
        if not user:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram để sử dụng bot này.")
            return
        
        if not context.args:
            update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /cleardebt @toan 500 hoặc /cleardebt @toan @hoa")
            return
            
        # Check if the last argument is a number (amount)
        has_amount = False
        try:
            amount = float(context.args[-1])
            has_amount = True
        except ValueError:
            amount = None
        
        # Extract usernames (all args except the last one if it's an amount)
        usernames = []
        for arg in context.args[:(len(context.args) - 1 if has_amount else len(context.args))]:
            if arg.startswith('@'):
                usernames.append(arg.replace('@', ''))
        
        if not usernames:
            update.message.reply_text("❌ Vui lòng chỉ định ít nhất một người. Ví dụ: /cleardebt @toan 500")
            return
        
        results = []
        for other in usernames:
            if amount:
                cursor.execute("""
                    DELETE FROM debts 
                    WHERE creditor = ? AND debtor = ? AND chat_id = ? AND amount <= ?
                    ORDER BY timestamp ASC
                    LIMIT 1
                """, (user, other, chat_id, amount))
                affected = cursor.rowcount
            else:
                cursor.execute(
                    "DELETE FROM debts WHERE creditor = ? AND debtor = ? AND chat_id = ?", 
                    (user, other, chat_id)
                )
                affected = cursor.rowcount
                
            other_display = get_display_name(other, chat_id)
            
            if affected > 0:
                if amount:
                    results.append(f"✅ Đã xóa {format_money(amount)} nợ từ {other_display}")
                else:
                    results.append(f"✅ Đã xóa toàn bộ khoản nợ với {other_display}")
            else:
                results.append(f"❌ Khôm tìm thấy khoản nợ nào với {other_display}")
            
        conn.commit()
        user_display = get_display_name(user, chat_id)
        
        # Compose response message
        if len(results) == 1:
            update.message.reply_text(results[0])
        else:
            msg = f"🧾 KẾT QUẢ XÓA NỢ CHO {user_display}:\n\n"
            for result in results:
                msg += f"{result}\n"
            update.message.reply_text(msg)
            
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")
        update.message.reply_text("❌ Cú pháp đúng: /cleardebt @username1 [@username2...] [số_tiền]")

def set_name(update, context):
    try:
        chat_id = update.effective_chat.id
        
        if len(context.args) < 2:
            update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /setname @toan 'Anh Nam'")
            return
            
        username = context.args[0].replace('@', '')
        display_name = " ".join(context.args[1:])
        
        cursor.execute(
            "INSERT OR REPLACE INTO name_mappings (username, display_name, chat_id) VALUES (?, ?, ?)",
            (username, display_name, chat_id)
        )
        conn.commit()
        update.message.reply_text(f"✅ Đã thiết lập tên hiển thị '@{username}' thành '{display_name}'")
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def history(update, context):
    chat_id = update.effective_chat.id
    
    # If username is provided as arg, show history for that user
    if context.args and not context.args[0].isdigit():
        username = context.args[0].replace('@', '')
        limit_arg = 1
    else:
        username = update.message.from_user.username
        limit_arg = 0
        if not username:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram hoặc chỉ định username. Ví dụ: /history @toan")
            return
    
    limit = 10  # Default number of transactions to show
    
    try:
        if context.args and limit_arg < len(context.args) and context.args[limit_arg].isdigit():
            limit = int(context.args[limit_arg])
    except:
        pass
    
    # Get transactions where user is either creditor or debtor
    cursor.execute("""
        SELECT creditor, debtor, amount, note, timestamp FROM debts 
        WHERE (creditor = ? OR debtor = ?) AND chat_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (username, username, chat_id, limit))
    
    transactions = cursor.fetchall()
    
    if not transactions:
        update.message.reply_text("Khôm có lịch sử giao dịch nào.")
        return
    
    display_name = get_display_name(username, chat_id)
    msg = f"📜 LỊCH SỬ GIAO DỊCH CỦA {display_name}\n\n"
    
    for cred, debt, amount, note, timestamp in transactions:
        try:
            dt = datetime.fromisoformat(timestamp)
            date_str = dt.strftime("%d/%m/%Y %H:%M")
        except:
            date_str = timestamp
            
        cred_display = get_display_name(cred, chat_id)
        debt_display = get_display_name(debt, chat_id)
        
        if cred == username:
            msg += f"{date_str}: {debt_display} nợ {amount} đ"
        else:
            msg += f"{date_str}: Nợ {cred_display} {amount} đ"
            
        if note:
            msg += f" ({note})"
        msg += "\n"
    
    update.message.reply_text(msg)

def group_summary(update, context):
    chat_id = update.effective_chat.id
    
    # Get all users involved in debts for this group
    cursor.execute("""
        SELECT DISTINCT username FROM (
            SELECT creditor as username FROM debts WHERE chat_id = ?
            UNION
            SELECT debtor as username FROM debts WHERE chat_id = ?
        )
    """, (chat_id, chat_id))
    
    users = [row[0] for row in cursor.fetchall()]
    
    if not users:
        update.message.reply_text("Khôm có khoản nợ nào trong nhóm này.")
        return
        
    msg = "📊 TỔNG HỢP NỢ TRONG NHÓM\n\n"
    
    # Calculate net balance for each user
    user_balances = []
    for user in users:
        # Get total amount this user owes to others
        cursor.execute("""
            SELECT SUM(amount) FROM debts 
            WHERE debtor = ? AND chat_id = ?
        """, (user, chat_id))
        total_owe = cursor.fetchone()[0] or 0
        
        # Get total amount others owe this user
        cursor.execute("""
            SELECT SUM(amount) FROM debts 
            WHERE creditor = ? AND chat_id = ?
        """, (user, chat_id))
        total_owned = cursor.fetchone()[0] or 0
        
        # Calculate net amount
        net = total_owned - total_owe
        user_balances.append((user, net))
    
    # Sort by net balance (highest positive first, then lowest negative)
    user_balances.sort(key=lambda x: x[1], reverse=True)
    
    # Split into positive and negative balances
    positive_balances = [(u, b) for u, b in user_balances if b > 0]
    negative_balances = [(u, b) for u, b in user_balances if b < 0]
    zero_balances = [(u, b) for u, b in user_balances if b == 0]
    
    # Format positive balances with visualization
    if positive_balances:
        msg += "🟢 NGƯỜI ĐƯỢC NHẬN:\n"
        max_balance = max([abs(b) for _, b in positive_balances]) if positive_balances else 0
        max_name_length = max([len(get_display_name(u, chat_id)) for u, _ in positive_balances])
        
        for user, balance in positive_balances:
            user_display = get_display_name(user, chat_id)
            bar_count = int(10 * balance / max_balance) if max_balance > 0 else 0
            bar = "█" * bar_count
            msg += f" - {user_display.ljust(max_name_length)} : +{format_money(balance)} {bar}\n"
    
    # Format negative balances with visualization
    if negative_balances:
        msg += "\n🔴 NGƯỜI CẦN TRẢ:\n"
        max_balance = max([abs(b) for _, b in negative_balances]) if negative_balances else 0
        max_name_length = max([len(get_display_name(u, chat_id)) for u, _ in negative_balances])
        
        for user, balance in negative_balances:
            user_display = get_display_name(user, chat_id)
            bar_count = int(10 * abs(balance) / max_balance) if max_balance > 0 else 0
            bar = "█" * bar_count
            msg += f" - {user_display.ljust(max_name_length)} : {format_money(balance)} {bar}\n"
    
    # Zero balances
    if zero_balances:
        msg += "\n⚪ Khôm NỢ/ĐƯỢC NỢ:\n"
        for user, _ in zero_balances:
            user_display = get_display_name(user, chat_id)
            msg += f" - {user_display}: 0 đ\n"
    
    update.message.reply_text(msg)

def divide_expense(update, context):
    try:
        chat_id = update.effective_chat.id
        creditor = update.message.from_user.username
        
        if not creditor:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram để sử dụng bot này.")
            return
            
        if len(context.args) < 2:
            update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /divide 500 @toan @quy @tuan [Tiền ăn]")
            return
            
        # Parse amount
        try:
            amount = float(context.args[0])
        except ValueError:
            update.message.reply_text("❌ Số tiền Khôm hợp lệ. Ví dụ: /divide 500 @toan @quy @tuan")
            return
            
        # Parse debtors (filter out any non-username args after the amount)
        debtors = []
        for arg in context.args[1:]:
            if arg.startswith('@'):
                debtors.append(arg.replace('@', ''))
        
        if not debtors:
            update.message.reply_text("❌ Vui lòng chỉ định ít nhất một người để chia tiền. Ví dụ: /divide 500 @toan @quy @tuan")
            return
            
        # Check if creditor is in the debtors list and remove if present
        include_self = False
        if creditor in debtors:
            debtors.remove(creditor)
            include_self = True
            
        if not debtors:
            update.message.reply_text("❌ Khôm thể chia tiền chỉ cho bản thân.")
            return
        
        # Only include sender in division if they were explicitly mentioned
        total_people = len(debtors)
        if include_self:
            total_people += 1  # Add one for creditor
        
        # Calculate amount per person (round up to avoid precision issues)
        amount_per_person = math.ceil((amount / total_people) * 100) / 100
        
        # Note extraction (anything after usernames)
        note_start_idx = 1 + len(context.args)
        for i in range(1, len(context.args)):
            if not context.args[i].startswith('@'):
                note_start_idx = i
                break
                
        note = " ".join(context.args[note_start_idx:]) if len(context.args) > note_start_idx else "Chia tiền"
        
        timestamp = datetime.now().isoformat()
        
        # Record debts for each debtor
        debtor_names = []
        for debtor in debtors:
            cursor.execute(
                "INSERT INTO debts (creditor, debtor, amount, note, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
                (creditor, debtor, amount_per_person, note, timestamp, chat_id)
            )
            debtor_names.append(get_display_name(debtor, chat_id))
            
        conn.commit()
        
        creditor_display = get_display_name(creditor, chat_id)
        debtor_list = ", ".join(debtor_names)
        
        # Compose a response message that explains the division
        total_amount = format_money(amount)
        per_person = format_money(amount_per_person)
        
        if include_self:
            creditor_share = amount_per_person
            others_pay = amount - creditor_share
            
            message = (
                f"✅ Đã chia {total_amount} cho {total_people} người (bao gồm {creditor_display}):\n\n"
                f"👉 Mỗi người trả {per_person}\n"
                f"💵 {creditor_display} trả {format_money(creditor_share)}\n"
                f"💰 Mọi người trả {creditor_display}: {format_money(others_pay)}\n"
            )
        else:
            message = (
                f"✅ Đã chia {total_amount} cho {total_people} người:\n\n"
                f"👉 Mỗi người trả {per_person}\n"
                f"💰 Mọi người trả {creditor_display}: {total_amount}\n"
            )
        
        if debtor_names:
            message += f"👥 Người nợ: {debtor_list}\n"
            
        if note and note != "Chia tiền":
            message += f"📝 Ghi chú: {note}"
            
        update.message.reply_text(message)
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def switch_model(update, context):
    """Switch between different AI models"""
    global llm
    
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("❌ Chỉ quản trị viên mới có thể thực hiện lệnh này.")
        return
        
    if not context.args or len(context.args) < 1:
        update.message.reply_text(
            f"❌ Vui lòng chỉ định tên mô hình: 'gemini', 'grok', hoặc 'deepseek'\n"
            f"Mô hình hiện tại: *{llm.model_name}*", 
            parse_mode='Markdown'
        )
        return
        
    model_name = context.args[0].lower()
    
    if model_name not in ['gemini', 'grok', 'deepseek']:
        update.message.reply_text(
            f"❌ Tên mô hình không hợp lệ: '{model_name}'. Vui lòng sử dụng 'gemini', 'grok', hoặc 'deepseek'.\n"
            f"Mô hình hiện tại: *{llm.model_name}*",
            parse_mode='Markdown'
        )
        return
        
    # Check if we already use this model
    if model_name == llm.model_name:
        update.message.reply_text(f"✅ Đã đang sử dụng mô hình *{model_name}*.", parse_mode='Markdown')
        return
        
    # Check if required API keys are set
    if model_name == 'gemini' and not os.getenv("GOOGLE_API_KEY"):
        update.message.reply_text("❌ GOOGLE_API_KEY chưa được thiết lập. Không thể chuyển sang Gemini.")
        return
    elif model_name == 'grok' and not os.getenv("GROK_API_KEY"):
        update.message.reply_text("❌ GROK_API_KEY chưa được thiết lập. Không thể chuyển sang Grok.")
        return
    elif model_name == 'deepseek':
        if not os.getenv("DEEPSEEK_API_KEY"):
            update.message.reply_text("❌ DEEPSEEK_API_KEY chưa được thiết lập. Không thể chuyển sang DeepSeek.")
            return
        
        # Check if OpenAI SDK is installed
        try:
            import openai
        except ImportError:
            update.message.reply_text("❌ Gói OpenAI chưa được cài đặt. Cài đặt bằng lệnh: `pip install openai`")
            return
        
    # Save the old model name for the message
    old_model = llm.model_name
    
    # Create a new LLMWrapper with the specified model
    llm = LLMWrapper(model_name)
    
    update.message.reply_text(
        f"✅ Đã chuyển từ mô hình *{old_model}* sang mô hình *{llm.model_name}*.",
        parse_mode='Markdown'
    )

def set_qr(update, context):
    try:
        chat_id = update.effective_chat.id
        username = update.message.from_user.username
        
        if not username:
            update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram để sử dụng bot này.")
            return
            
        if len(context.args) < 1:
            update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /setqr https://example.com/myqrcode.jpg")
            return
            
        image_url = context.args[0]
        
        # Validate URL by checking if it's accessible
        try:
            response = requests.head(image_url, timeout=5)
            if not response.ok or not response.headers.get('content-type', '').startswith('image/'):
                update.message.reply_text("❌ URL Khôm hợp lệ hoặc Khôm phải hình ảnh.")
                return
        except:
            update.message.reply_text("❌ Khôm thể truy cập URL. Vui lòng kiểm tra lại.")
            return
            
        # Store QR code URL
        cursor.execute(
            "INSERT OR REPLACE INTO qr_codes (username, image_url, chat_id) VALUES (?, ?, ?)",
            (username, image_url, chat_id)
        )
        conn.commit()
        
        update.message.reply_text("✅ Đã lưu QR code của bạn thành công.")
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def get_qr(update, context):
    try:
        chat_id = update.effective_chat.id
        
        # If username is provided as argument, get their QR
        if context.args and context.args[0].startswith('@'):
            target_username = context.args[0].replace('@', '')
        else:
            # Get the sender's QR
            target_username = update.message.from_user.username
            if not target_username:
                update.message.reply_text("❌ Bạn cần thiết lập username trên Telegram để sử dụng bot này.")
                return
        
        # Retrieve QR code URL
        cursor.execute(
            "SELECT image_url FROM qr_codes WHERE username = ? AND chat_id = ?", 
            (target_username, chat_id)
        )
        result = cursor.fetchone()
        
        if not result:
            display_name = get_display_name(target_username, chat_id)
            update.message.reply_text(f"❌ Khôm tìm thấy QR code cho {display_name}.")
            return
            
        image_url = result[0]
        display_name = get_display_name(target_username, chat_id)
        
        # Send QR code as photo
        try:
            response = requests.get(image_url)
            if response.ok:
                update.message.reply_photo(
                    photo=BytesIO(response.content),
                    caption=f"QR code của {display_name}"
                )
            else:
                update.message.reply_text(f"❌ Khôm thể tải QR code. URL Khôm hợp lệ hoặc đã hết hạn.")
        except Exception as e:
            update.message.reply_text(f"❌ Lỗi khi tải QR code: {str(e)}")
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {str(e)}")

def help_command(update, context):
    # Basic help text for all users
    help_text = """
📋 *HƯỚNG DẪN SỬ DỤNG BOT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 *QUẢN LÝ NỢ*
• /adddebt <số_tiền> @username1 [@username2...] [ghi_chú] - Ghi nợ cho một hoặc nhiều người
  _Ví dụ: /adddebt 500 @toan Trà sữa_
  _Ví dụ: /adddebt 10000 @toan @quy @tuan Tiền ăn_

• /divide <số_tiền> @user1 @user2 [ghi_chú] - Chia tiền đều cho những người được chỉ định
  _Ví dụ: /divide 900 @toan @quy @tuan Tiền ăn trưa_
  _Chỉ bao gồm bạn trong phép tính nếu bạn được tag trong lệnh_

• /cleardebt @username1 [@username2...] [số_tiền] - Xóa khoản nợ (cho một hoặc nhiều người)
  _Ví dụ: /cleardebt @toan 500_
  _Ví dụ: /cleardebt @toan @quy @tuan 200_
  _Ví dụ: /cleardebt @toan @quy (xóa toàn bộ)_

📊 *XEM THÔNG TIN*
• /summary [@username] - Xem tổng hợp nợ của bạn (hoặc người khác)
  _Ví dụ: /summary hoặc /summary @toan_

• /history [@username] [số_lượng] - Xem lịch sử giao dịch
  _Ví dụ: /history hoặc /history @toan 20_
  
• /groupsum - Xem tổng hợp nợ của cả nhóm (chỉ dùng trong nhóm)

🔄 *QR CODE*
• /setqr <url_hình_ảnh> - Lưu URL hình ảnh QR code của bạn
  _Ví dụ: /setqr https://example.com/myqrcode.jpg_

• /qr - Xem QR code của bạn
  _Ví dụ: /qr_

• /get @username qr - Xem QR code của người khác
  _Ví dụ: /get @toan qr_

⚙️ *CÀI ĐẶT*
• /setname @username tên_hiển_thị - Đặt tên hiển thị
  _Ví dụ: /setname @toan Anh Toàn_

💡 *Mẹo*: 
- QR code có thể là ảnh mã QR thanh toán từ ví điện tử của bạn
- Dữ liệu được lưu trong database trên Railway
"""

    # Additional admin help text
    admin_help = """
🛠️ *ADMIN COMMANDS*
• /status - Xem trạng thái hệ thống và thông tin database
• /shutdown - Tắt bot an toàn
• /backup - Sao lưu database và gửi file về telegram để khôi phục
• /switchmodel <model_name> - Switch between AI models (gemini, grok, deepseek)

⚡️ *Model Configuration*
Bot can use different AI models. Current model: *{0}*
To change models:
• Use /switchmodel gemini, /switchmodel grok, or /switchmodel deepseek

Note: 
- Gemini requires a valid GOOGLE_API_KEY environment variable
- Grok requires a valid GROK_API_KEY environment variable
- DeepSeek requires a valid DEEPSEEK_API_KEY environment variable and OpenAI package (pip install openai)
"""
    
    # Add admin help if user is admin
    if update.effective_user and update.effective_user.id in ADMIN_IDS:
        help_text += admin_help.format(llm.model_name.capitalize())
    
    try:
        update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        # If Markdown parsing fails, send without formatting
        print(f"Error sending help with Markdown: {e}")
        update.message.reply_text(help_text)

# ====== Admin Commands ======

def status_command(update, context):
    if update.effective_user.id in ADMIN_IDS:
        # Get uptime
        current_time = datetime.now()
        uptime = current_time - start_time
        days, remainder = divmod(uptime.total_seconds(), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        # Get database info
        conn_status = sqlite3.connect(DB_PATH)
        c = conn_status.cursor()
        c.execute("SELECT COUNT(*) FROM debts")
        debt_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM name_mappings")
        name_count = c.fetchone()[0]
        conn_status.close()
        
        # Get model-specific details
        model_details = ""
        if llm.model_name == 'gemini':
            model_details = "- Model ID: gemini-2.0-flash"
        elif llm.model_name == 'grok':
            model_details = f"- Model ID: {llm.model_id}"
        elif llm.model_name == 'deepseek':
            model_details = f"- Model ID: {llm.model_id} (DeepSeek-V3)\n- API: Using OpenAI SDK with DeepSeek endpoint"
        
        # Format message
        status = f"""
📊 *BOT STATUS*

⏱️ *Uptime*: {int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s

💾 *Database*: 
- Location: {DB_PATH} (Railway mounted volume)
- Debts: {debt_count} records
- Names: {name_count} mappings

🤖 *Process*:
- PID: {os.getpid()}
- Admin IDs: {ADMIN_IDS}

🧠 *AI Model*:
- Current: {llm.model_name.capitalize()}
{model_details}
"""
        try:
            update.message.reply_text(status, parse_mode='Markdown')
        except Exception as e:
            # If Markdown parsing fails, send without formatting
            print(f"Error sending status with Markdown: {e}")
            update.message.reply_text(status)
    else:
        update.message.reply_text("❌ Only admins can view detailed status.")

def shutdown_command(update, context):
    if update.effective_user.id in ADMIN_IDS:
        update.message.reply_text("⚠️ Shutting down bot...")
            
        # Schedule shutdown after messages are sent
        def shutdown():
            time.sleep(2)  # Wait for messages to be sent
            updater.stop()
            updater.is_idle = False
            
        threading.Thread(target=shutdown).start()
    else:
        update.message.reply_text("❌ Only admins can shut down the bot.")

def backup_database(update, context):
    global cursor, conn
    
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("❌ Chỉ Admin mới có thể sao lưu database.")
        return
        
    try:
        # Create timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"debtbot_backup_{timestamp}.db"
        backup_path = os.path.join(DB_DIR, backup_filename)
        
        # Create backup
        update.message.reply_text("⏳ Đang tạo bản sao lưu database...")
        
        # Close the current connection to ensure all transactions are committed
        conn.close()
        
        # Create a copy of the database file
        shutil.copy2(DB_PATH, backup_path)
        
        # Reopen the database connection
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        cursor = conn.cursor()
        
        # Send file to admin
        with open(backup_path, 'rb') as backup_file:
            update.message.reply_document(
                document=backup_file,
                filename=backup_filename,
                caption=f"📦 Database backup - {timestamp}"
            )
            
        # Delete the temporary backup file after sending
        os.remove(backup_path)
        
        # Inform user about manual restore process
        update.message.reply_text(
            "✅ Sao lưu hoàn tất!\n\n"
        )
    except Exception as e:
        # Make sure to reopen connection if there was an error
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
        except:
            pass
        update.message.reply_text(f"❌ Lỗi khi sao lưu: {str(e)}")

# ====== LLM Chat Handler ======

def extract_command_from_text(text):
    """Extract potential command patterns from natural language text."""
    patterns = {
        r'(?i)add.*debt.*(\d+).*to.*@(\w+)': '/adddebt',
        r'(?i)divide.*(\d+).*between.*(@\w+(?:\s+@\w+)*)': '/divide',
        r'(?i)clear.*debt.*@(\w+)': '/cleardebt',
        r'(?i)show.*summary.*(?:for.*)?(@\w+)?': '/summary',
        r'(?i)show.*history.*(?:for.*)?(@\w+)?': '/history',
        r'(?i)show.*group.*summary': '/groupsum',
        r'(?i)set.*name.*@(\w+).*to.*(.+)': '/setname',
        r'(?i)set.*qr.*(?:code)?.*to.*(\S+)': '/setqr',
        r'(?i)show.*qr.*(?:code)?.*(?:for.*)?(@\w+)?': '/qr',
        r'(?i)help': '/help'
    }
    
    for pattern, command in patterns.items():
        match = re.search(pattern, text)
        if match:
            return command, match.groups()
    return None, None

def handle_llm_chat(update, context):
    """Handle natural language chat with the bot using Gemini."""
    message = update.message
    if not message or not message.text:
        return

    # Get bot's username and possible mention formats
    bot_username = context.bot.username
    possible_mentions = [
        f"@{bot_username}",
        f"@{bot_username.lower()}",
        f"@{bot_username.upper()}"
    ]
    
    # Check if any of the possible mentions are in the message
    mentioned = any(mention in message.text.lower() for mention in [m.lower() for m in possible_mentions])
    if not mentioned:
        return
    
    # Get the actual mention used
    used_mention = next((m for m in possible_mentions if m.lower() in message.text.lower()), possible_mentions[0])
    
    # Remove bot mention from text
    user_text = message.text.replace(used_mention, "").strip()
    
    # If no actual message after mention, ignore
    if not user_text:
        update.message.reply_text("🙏 Dạ đại ca cần nô tỳ giúp gì ạ? Nô tỳ luôn sẵn lòng phụng sự đại ca anh minh!")
        return
    
    try:
        # First try to extract command pattern
        command, args = extract_command_from_text(user_text)
        
        if command:
            # Convert natural language to command
            if args:
                command_text = f"{command} {' '.join(args)}"
            else:
                command_text = command
                
            # Execute the command by calling the appropriate function
            execute_command(command_text, update, context)
            return
            
        # If no command pattern found, use Gemini for intent recognition and command generation
        prompt = f"""You are a Vietnamese debt management bot that can also chat about other topics. Analyze this user message: "{user_text}"

Step 1: Determine if this is a debt-related question or command.

If it IS debt-related, follow these instructions:
Your task is to:
1. Determine what debt management action the user wants to perform
2. Convert it to the appropriate command format
3. Extract all relevant parameters

Available Commands and their formats:
- /adddebt <amount> @username1 [@username2...] [note] - Add debt
- /divide <amount> @user1 @user2... [note] - Split an expense
- /cleardebt @username1 [@username2...] [amount] - Clear debt
- /summary [@username] - View debt summary
- /history [@username] [count] - View transaction history
- /groupsum - View group summary
- /setname @username display_name - Set display name
- /setqr url - Set QR code
- /qr [@username] - View QR code
- /help - View help

If you can determine a debt-related command, respond ONLY with the exact command to execute (e.g., "/adddebt 500 @toan Trà sữa") and nothing else.

If the message is NOT debt-related but about another topic like food, general chat, questions, etc., respond with:
"CHAT: " followed by a conversational, friendly reply in Vietnamese where you:
- Stay in character as a Vietnamese debt bot who can also chat about other topics
- Use "nô tỳ" for self-reference
- Use "đại ca" for user reference
- Be helpful, informative, and engaging about the topic
- Add personality and humor appropriate to the context
- Include one appropriate emoji
- If asked about food, you can share Vietnamese food suggestions or recipes
- If it's casual chat, respond in a friendly, playful manner
- If asked a question, provide helpful information if you know it

Examples:
- "I owe Toan 50k for coffee" → "/adddebt 50000 @toan Coffee"
- "Split 90k bill between me, Quy and Tuan" → "/divide 90000 @quy @tuan Shared bill"
- "How much do I owe?" → "/summary"
- "What's a good Vietnamese food to try?" → "CHAT: Dạ đại ca, nô tỳ xin phép được giới thiệu món phở - tinh hoa ẩm thực Việt Nam! Nước dùng ngọt thanh, bánh phở dai mềm, ăn kèm rau thơm tươi mát. Nếu đại ca thích món cay, có thể thử bún bò Huế hoặc bún riêu cua ạ! 🍜"
- "How are you today?" → "CHAT: Ôi chao ôi, đại ca đã quan tâm đến sức khỏe của nô tỳ! Nô tỳ khỏe re như trâu đồng, sẵn sàng phục vụ đại ca với năng lượng tràn đầy! Đại ca hôm nay thế nào ạ? 😊"
"""
        
        response = llm.generate_content(prompt)
        if response and response.text:
            response_text = response.text.strip()
            
            # Check if response starts with a command
            if response_text.startswith('/'):
                # Extract command and execute it directly
                execute_command(response_text, update, context)
            # Check if it's a chat response
            elif response_text.startswith('CHAT:'):
                chat_response = response_text[5:].strip()
                update.message.reply_text(chat_response)
            else:
                update.message.reply_text(response_text)
        else:
            update.message.reply_text("🙏 Nô tỳ xin lỗi đại ca, trí thông minh nhỏ bé của nô tỳ không thể xử lý yêu cầu cao siêu của ngài lúc này! 😭")
        
    except Exception as e:
        print(f"Error in LLM chat: {e}")
        update.message.reply_text("🙏 Nô tỳ xin lỗi đại ca anh minh! Trí óc tầm thường của nô tỳ không hiểu được ý của ngài. Xin đại ca từ bi hạ cố chỉ dạy lại hoặc gõ /help để nô tỳ được hầu hạ đúng cách! ‍♂️")

def execute_command(command_text, update, context):
    """Execute a command by directly calling the appropriate function."""
    # Map of command names to their handler functions
    command_handlers = {
        '/adddebt': add_debt,
        '/divide': divide_expense,
        '/cleardebt': clear_debt,
        '/summary': summary,
        '/history': history,
        '/groupsum': group_summary,
        '/setname': set_name,
        '/setqr': set_qr,
        '/qr': get_qr,
        '/get': get_qr,
        '/help': help_command,
        '/status': status_command,
        '/backup': backup_database,
        '/shutdown': shutdown_command,
        '/switchmodel': switch_model
    }
    
    # Parse the command and arguments
    parts = command_text.split()
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    
    # Find the appropriate handler function
    handler = command_handlers.get(command)
    
    if handler:
        # Store the original args and text
        original_args = context.args
        original_text = update.message.text
        
        try:
            # Set the context args to our parsed args
            context.args = args
            # Set the message text to our command
            update.message.text = command_text
            # Call the handler directly
            handler(update, context)
            
            # Generate a friendly follow-up response after successful command execution
            generate_follow_up_response(command, args, update, context)
        finally:
            # Restore original values
            context.args = original_args
            update.message.text = original_text
    else:
        update.message.reply_text(f"🤔 Nô tỳ không hiểu lệnh '{command}'. Xin hãy thử lại hoặc gõ /help để xem hướng dẫn.")

def generate_follow_up_response(command, args, update, context):
    """Generate a friendly follow-up response after executing a command."""
    command_info = {
        '/adddebt': "adding debt",
        '/divide': "dividing expenses",
        '/cleardebt': "clearing debt",
        '/summary': "summarizing debts",
        '/history': "showing transaction history",
        '/groupsum': "showing group summary",
        '/setname': "setting display name",
        '/setqr': "setting QR code",
        '/qr': "retrieving QR code",
        '/get': "retrieving information",
        '/help': "showing help",
    }
    
    action = command_info.get(command, "processing request")
    
    prompt = f"""As a humorous, playful Vietnamese debt management bot, generate a very funny, over-the-top follow-up response 
after successfully {action}. 

Key details:
- Command executed: {command}
- Arguments: {' '.join(args)}
- User: {update.message.from_user.first_name or 'User'}

Response style REQUIREMENTS (absolutely must follow):
- Always use Vietnamese
- ALWAYS use "nô tỳ" for self-reference
- ALWAYS use "đại ca" for user reference
- Speak like a loyal, slightly silly servant to a master
- Be extremely flattering and overly dramatic
- Include exaggerated compliments about the user's brilliance, wisdom, or generosity
- Use funny, exaggerated expressions and metaphors
- Add humorous, theatrical flourishes
- Include at least one appropriate emoji
- Keep it short (1-2 sentences maximum)
- Don't repeat information the command already showed
- Don't mention the command name directly

Examples:
- After adding debt: "✨ Nô tỳ đã ghi sổ cẩn thận rồi ạ! Trí nhớ siêu phàm của đại ca thật khiến nô tỳ ngưỡng mộ vô cùng! 🙇‍♂️"
- After dividing expense: "🌟 Ôi, đại ca tính toán quá thông minh! Nô tỳ đã chia đều tài sản như Tôn Ngộ Không chia đào tiên vậy! 🙈"
- After showing summary: "👑 Báo cáo đại ca anh minh! Kho báu của ngài đang chờ thu hồi, đại ca thật giàu có tuyệt vời! 💰"
- After clearing debt: "🎉 Ối giời ơi! Đại ca vừa xóa nợ ư? Tấm lòng bao dung của đại ca còn to hơn cả biển Đông! 😍"
"""
    
    try:
        response = llm.generate_content(prompt)
        if response and response.text:
            follow_up = response.text.strip()
            # Allow slightly longer responses for humorous content
            if len(follow_up) < 200:
                update.message.reply_text(follow_up)
    except Exception as e:
        print(f"Error generating follow-up response: {e}")
        # Silently fail - don't send an error message to avoid confusion

# ====== Main Bot Setup ======

def main():
    TOKEN = os.getenv("TOKEN") 
    
    # Set global updater and admin IDs
    global updater, ADMIN_IDS
    
    # Track start time for uptime calculation
    global start_time
    start_time = datetime.now()
    
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    # Set up error handler
    def error_handler(update, context):
        try:
            # Log error
            print(f"Error: {context.error}")
            
            # If update is available, send error message to chat
            if update and update.effective_chat:
                # Only notify admins about errors
                if update.effective_user and update.effective_user.id in ADMIN_IDS:
                    update.effective_message.reply_text(
                        f"⚠️ An error occurred: {context.error}"
                    )
        except:
            print("Critical error in error handler")
    
    dp.add_error_handler(error_handler)

    # Add LLM chat handler in a higher group number (1) so it runs after command handlers
    #add mention filter

    dp.add_handler(MessageHandler(
        Filters.text & ~Filters.command & Filters.entity("mention"),
        handle_llm_chat
    ))

    # Regular commands
    dp.add_handler(CommandHandler("adddebt", add_debt, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("summary", summary, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("cleardebt", clear_debt, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("setname", set_name, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("history", history, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("groupsum", group_summary, filters=Filters.chat_type.groups))
    dp.add_handler(CommandHandler("divide", divide_expense, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("setqr", set_qr, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("qr", get_qr, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("get", get_qr, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("help", help_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    
    # Admin commands
    dp.add_handler(CommandHandler("shutdown", shutdown_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("status", status_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("backup", backup_database, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("switchmodel", switch_model, filters=Filters.chat_type.groups | Filters.chat_type.private))

    print("Bot started. Press Ctrl+C to stop.")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
