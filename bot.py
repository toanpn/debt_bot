import sqlite3
from telegram.ext import Updater, CommandHandler, Filters
from datetime import datetime
import math
import requests
from io import BytesIO
import os
import shutil
import time
import threading
import sys
import atexit

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

# Set database path - use environment variable or default to data directory
DB_DIR = os.environ.get('DB_DIR', os.path.join(os.path.expanduser('~'), 'bot_data'))
DB_PATH = os.environ.get('DB_PATH', os.path.join(DB_DIR, 'debtbot.db'))
BACKUP_DIR = os.environ.get('BACKUP_DIR', os.path.join(DB_DIR, 'backups'))

# Ensure directories exist
os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
print(f"Using database at: {DB_PATH}")
print(f"Using backup directory: {BACKUP_DIR}")

# Check if another instance is running
check_instance()

# Backup function
def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"debtbot_backup_{timestamp}.db")
    
    try:
        # Create a connection to make sure all transactions are saved
        temp_conn = sqlite3.connect(DB_PATH)
        temp_conn.close()
        
        # Copy the database file
        shutil.copy2(DB_PATH, backup_path)
        
        # Keep only the 5 most recent backups
        backups = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) 
                          if f.startswith("debtbot_backup_") and f.endswith(".db")])
        
        if len(backups) > 5:
            for old_backup in backups[:-5]:
                os.remove(old_backup)
                
        print(f"Database backed up to {backup_path}")
        return True
    except Exception as e:
        print(f"Backup failed: {str(e)}")
        return False

# Periodic backup function
def scheduled_backup():
    while True:
        time.sleep(3600)  # Backup every hour
        backup_database()

# Start backup thread
backup_thread = threading.Thread(target=scheduled_backup, daemon=True)
backup_thread.start()

# Create initial backup
backup_database()

# ====== DB Migration ======
def migrate_database():
    print("Performing complete database migration...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create a backup of the debts table
    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS debts_backup AS SELECT * FROM debts")
        cursor.execute("DROP TABLE debts")
        print("Created backup of debts table")
    except sqlite3.OperationalError as e:
        print(f"Backup note: {e}")
    
    # Create the new debts table with chat_id
    cursor.execute("""
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
    
    # Copy data from backup to new table
    try:
        cursor.execute("""
        INSERT INTO debts (id, creditor, debtor, amount, note, timestamp, chat_id)
        SELECT id, creditor, debtor, amount, note, timestamp, 0 FROM debts_backup
        """)
        print("Migrated debt data")
    except sqlite3.OperationalError as e:
        print(f"Data migration note: {e}")
    
    # Create a backup of name_mappings table
    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS name_mappings_backup AS SELECT * FROM name_mappings")
        cursor.execute("DROP TABLE name_mappings")
        print("Created backup of name_mappings table")
    except sqlite3.OperationalError as e:
        print(f"Name backup note: {e}")
    
    # Create the new name_mappings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS name_mappings (
        username TEXT,
        display_name TEXT,
        chat_id INTEGER DEFAULT 0,
        PRIMARY KEY (username, chat_id)
    )
    """)
    
    # Copy data from backup to new table
    try:
        cursor.execute("""
        INSERT INTO name_mappings (username, display_name, chat_id)
        SELECT username, display_name, 0 FROM name_mappings_backup
        """)
        print("Migrated name mapping data")
    except sqlite3.OperationalError as e:
        print(f"Name data migration note: {e}")
    
    # Create QR codes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS qr_codes (
        username TEXT,
        image_url TEXT,
        chat_id INTEGER,
        PRIMARY KEY (username, chat_id)
    )
    """)
    
    conn.commit()
    conn.close()
    print("Migration completed")

# Run migration
migrate_database()

# ====== DB Setup ======
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
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
            
        debtor = context.args[0].replace('@', '')
        amount = float(context.args[1])
        note = " ".join(context.args[2:]) if len(context.args) > 2 else ""
        timestamp = datetime.now().isoformat()
        
        if creditor.lower() == debtor.lower():
            update.message.reply_text("❌ Khôm thể ghi nợ chính mình.")
            return

        cursor.execute(
            "INSERT INTO debts (creditor, debtor, amount, note, timestamp, chat_id) VALUES (?, ?, ?, ?, ?, ?)",
            (creditor, debtor, amount, note, timestamp, chat_id)
        )
        conn.commit()
        
        creditor_display = get_display_name(creditor, chat_id)
        debtor_display = get_display_name(debtor, chat_id)
        
        update.message.reply_text(
            f"✅ {creditor_display} đã ghi nợ {debtor_display} {amount} đ" + (f" cho {note}" if note else "")
        )
    except IndexError:
        update.message.reply_text("❌ Sai cú pháp gòi. Ví dụ: /adddebt @toan 500 Trà sữa")
    except ValueError:
        update.message.reply_text("❌ Số tiền Khôm hợp lệ. Ví dụ: /adddebt @toan 500 Trà sữa")
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
📋 *HƯỚNG DẪN SỬ DỤNG BOT CHO HỘI NGƯỜI HÈN VN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💰 *QUẢN LÝ NỢ*
• `/adddebt @username <số_tiền> [ghi_chú]` - Ghi nợ cho người dùng
  _Ví dụ: /adddebt @toan 500 Trà sữa_

• `/divide <số_tiền> @user1 @user2 [ghi_chú]` - Chia tiền đều cho những người được chỉ định
  _Ví dụ: /divide 900 @toan @quy @tuan Tiền ăn trưa_
  _Chỉ bao gồm bạn trong phép tính nếu bạn được tag trong lệnh_

• `/cleardebt @username1 [@username2...] [số_tiền]` - Xóa khoản nợ (cho một hoặc nhiều người)
  _Ví dụ: /cleardebt @toan 500_
  _Ví dụ: /cleardebt @toan @quy @tuan 200_
  _Ví dụ: /cleardebt @toan @quy_ (xóa toàn bộ)

📊 *XEM THÔNG TIN*
• `/summary [@username]` - Xem tổng hợp nợ của bạn (hoặc người khác)
  _Ví dụ: /summary hoặc /summary @toan_

• `/history [@username] [số_lượng]` - Xem lịch sử giao dịch
  _Ví dụ: /history hoặc /history @toan 20_
  
• `/groupsum` - Xem tổng hợp nợ của cả nhóm (chỉ dùng trong nhóm)

🔄 *QR CODE*
• `/setqr <url_hình_ảnh>` - Lưu URL hình ảnh QR code của bạn
  _Ví dụ: /setqr https://example.com/myqrcode.jpg_

• `/qr` - Xem QR code của bạn
  _Ví dụ: /qr_

• `/get @username qr` - Xem QR code của người khác
  _Ví dụ: /get @toan qr_

⚙️ *CÀI ĐẶT*
• `/setname @username tên_hiển_thị` - Đặt tên hiển thị
  _Ví dụ: /setname @toan Anh Toàn_

💡 *Mẹo*: 
- QR code có thể là ảnh mã QR thanh toán từ ví điện tử của bạn
- Dữ liệu được lưu tại: {DB_PATH}
- Backup tự động mỗi giờ và khi khởi động

🛠️ *ADMIN COMMANDS*
• `/backup` - Tạo bản sao lưu cơ sở dữ liệu thủ công
• `/restore` - Xem và khôi phục dữ liệu từ bản sao lưu
• `/status` - Xem trạng thái hệ thống và thông tin database
• `/shutdown` - Tắt bot an toàn (tự động backup trước khi tắt)
"""

    # Replace the DB_PATH placeholder with actual path
    help_text = help_text.replace("{DB_PATH}", DB_PATH)

    update.message.reply_text(help_text, parse_mode='Markdown')

# ====== Admin Commands ======

def backup_command(update, context):
    # Check if user is admin (you can modify this check as needed)
    if update.effective_user.id in ADMIN_IDS:
        if backup_database():
            update.message.reply_text("✅ Database backed up successfully.")
        else:
            update.message.reply_text("❌ Backup failed.")
    else:
        update.message.reply_text("❌ Only admins can use this command.")

def restore_command(update, context):
    # Check if user is admin
    if update.effective_user.id not in ADMIN_IDS:
        update.message.reply_text("❌ Only admins can use this command.")
        return
        
    # List available backups
    try:
        backups = sorted([f for f in os.listdir(BACKUP_DIR) 
                      if f.startswith("debtbot_backup_") and f.endswith(".db")])
        
        if not backups:
            update.message.reply_text("❌ No backups found.")
            return
            
        if not context.args:
            # Show list of available backups
            msg = "Available backups:\n\n"
            for i, backup in enumerate(backups):
                # Extract timestamp from filename
                timestamp = backup.replace("debtbot_backup_", "").replace(".db", "")
                # Format it for display
                try:
                    dt = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
                    formatted_time = dt.strftime("%d/%m/%Y %H:%M:%S")
                    msg += f"{i+1}. {formatted_time}\n"
                except:
                    msg += f"{i+1}. {timestamp}\n"
                    
            msg += "\nUse /restore [number] to restore a backup"
            update.message.reply_text(msg)
            return
            
        # Parse backup number
        try:
            backup_index = int(context.args[0]) - 1
            if backup_index < 0 or backup_index >= len(backups):
                update.message.reply_text("❌ Invalid backup number.")
                return
                
            backup_file = backups[backup_index]
            backup_path = os.path.join(BACKUP_DIR, backup_file)
            
            # Close current connection
            global conn, cursor
            conn.close()
            
            # Backup current DB before restoring
            emergency_backup_path = os.path.join(BACKUP_DIR, f"emergency_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            shutil.copy2(DB_PATH, emergency_backup_path)
            
            # Restore from backup
            shutil.copy2(backup_path, DB_PATH)
            
            # Reconnect to DB
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            cursor = conn.cursor()
            
            update.message.reply_text(f"✅ Database restored from backup: {backup_file}")
        except ValueError:
            update.message.reply_text("❌ Please provide a valid backup number.")
        except Exception as e:
            update.message.reply_text(f"❌ Restore failed: {str(e)}")
    except Exception as e:
        update.message.reply_text(f"❌ Error: {str(e)}")

def shutdown_command(update, context):
    if update.effective_user.id in ADMIN_IDS:
        update.message.reply_text("⚠️ Shutting down bot. Please wait...")
        
        # Perform backup before shutdown
        if backup_database():
            update.message.reply_text("✅ Final backup completed.")
        else:
            update.message.reply_text("⚠️ Final backup failed, shutting down anyway.")
            
        # Schedule shutdown after messages are sent
        def shutdown():
            time.sleep(2)  # Wait for messages to be sent
            updater.stop()
            updater.is_idle = False
            
        threading.Thread(target=shutdown).start()
    else:
        update.message.reply_text("❌ Only admins can shut down the bot.")

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
        
        # Get backup info
        backup_count = len([f for f in os.listdir(BACKUP_DIR) 
                           if f.startswith("debtbot_backup_") and f.endswith(".db")])
        
        # Format message
        status = f"""
📊 *BOT STATUS*

⏱️ *Uptime*: {int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s

💾 *Database*: 
- Location: `{DB_PATH}`
- Debts: {debt_count} records
- Names: {name_count} mappings

🔄 *Backups*:
- Location: `{BACKUP_DIR}`
- Count: {backup_count} backups
- Auto-backup: Every hour

🤖 *Process*:
- PID: {os.getpid()}
- Admin IDs: {ADMIN_IDS}
"""
        update.message.reply_text(status, parse_mode='Markdown')
    else:
        update.message.reply_text("❌ Only admins can view detailed status.")

# ====== Main Bot Setup ======

def main():
    TOKEN = "8123653342:AAHibawwr85tnUHUyHP3Eowghod2OicBqJg"  # <-- Bệ hạ nhớ dán token bot ở đây
    
    # Define your admin user IDs here
    global ADMIN_IDS
    ADMIN_IDS = [1095200180]  # Replace with your Telegram user ID
    
    # Track start time for uptime calculation
    global start_time
    start_time = datetime.now()
    
    global updater
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
    dp.add_handler(CommandHandler("backup", backup_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("restore", restore_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("shutdown", shutdown_command, filters=Filters.chat_type.groups | Filters.chat_type.private))
    dp.add_handler(CommandHandler("status", status_command, filters=Filters.chat_type.groups | Filters.chat_type.private))

    print("Bot started. Press Ctrl+C to stop.")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
