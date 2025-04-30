# Debt Bot

A Telegram bot for tracking debts among friends and groups with natural language support using AI models like Google's Gemini, X.AI's Grok, or DeepSeek.

## Features

- 💰 Track debts between users
- 📊 View debt summaries and balances with visual representations
- 📝 Add notes to debt records
- 🧾 View transaction history
- 👥 Divide expenses among multiple users
- 📱 Set and display QR codes for payments
- 🔄 Clear debts with other users
- 👤 Set custom display names
- 🤖 Natural language processing using AI (Gemini, Grok, or DeepSeek)
- 📦 Database backup and restoration
- 💾 Multi-group support with data isolation
- 🔄 Support for multiple AI models with easy switching

## Commands

### 💰 Debt Management
- `/adddebt <số_tiền> @username1 [@username2...] [ghi_chú]` - Add debt for one or more users
  - Example: `/adddebt 500 @toan Trà sữa`
  - Example: `/adddebt 10000 @toan @quy @tuan Tiền ăn`

- `/divide <số_tiền> @user1 @user2... [ghi_chú]` - Split expenses equally among specified users
  - Example: `/divide 900 @toan @quy @tuan Tiền ăn trưa`
  - Note: Only includes you in the calculation if you're tagged in the command

- `/cleardebt @username1 [@username2...] [số_tiền]` - Clear debts (for one or more users)
  - Example: `/cleardebt @toan 500`
  - Example: `/cleardebt @toan @quy @tuan 200`
  - Example: `/cleardebt @toan @quy` (clears all debts)

### 📊 Information Display
- `/summary [@username]` - View debt summary for yourself or another user
  - Example: `/summary` or `/summary @toan`

- `/history [@username] [số_lượng]` - View transaction history
  - Example: `/history` or `/history @toan 20`
  
- `/groupsum` - View group debt summary (group chats only)

### 🔄 QR Codes
- `/setqr <url_hình_ảnh>` - Save your QR code image URL
  - Example: `/setqr https://example.com/myqrcode.jpg`

- `/qr` - View your QR code
  - Example: `/qr`

- `/get @username qr` - View someone else's QR code
  - Example: `/get @toan qr`

### ⚙️ Settings
- `/setname @username tên_hiển_thị` - Set display name
  - Example: `/setname @toan Anh Toàn`

- `/help` - View help information

### 🛠️ Admin Commands
- `/status` - View system status and database information
- `/shutdown` - Safely shut down the bot
- `/backup` - Back up database and send the file via Telegram
- `/switchmodel <model_name>` - Switch between AI models (gemini, grok, or deepseek)

## Setup

### Environment Variables
- `TOKEN` - Your Telegram bot token
- `DB_DIR` - Directory to store the database (defaults to ~/bot_data)
- `GOOGLE_API_KEY` - Google API key for Gemini AI integration
- `GROK_API_KEY` - X.AI API key for Grok AI integration
- `MODEL_CHOICE` - AI model to use (default: "gemini", options: "gemini", "grok", or "deepseek")
- `ADMIN_IDS` - Comma-separated Fixed in code, list of Telegram user IDs with admin privileges

### AI Model Configuration
The bot supports multiple AI models for natural language processing:

1. **Gemini** (default)
   - Requires `GOOGLE_API_KEY` environment variable
   - Uses Google's Gemini 2.0 Flash model
   - Set with `MODEL_CHOICE=gemini`

2. **Grok**
   - Requires `GROK_API_KEY` environment variable
   - Uses X.AI's Grok-3-mini-beta model
   - Set with `MODEL_CHOICE=grok`

3. **DeepSeek**
   - Requires `DEEPSEEK_API_KEY` environment variable
   - Uses DeepSeek-V3 model (deepseek-chat)
   - Set with `MODEL_CHOICE=deepseek`

Admins can switch models during runtime using the `/switchmodel` command.

### Database
The bot uses SQLite for data storage with the following tables:
- `debts` - Stores debt records with chat_id support for multi-group functionality
- `name_mappings` - Maps usernames to display names in specific chat contexts
- `qr_codes` - Stores payment QR codes for users

## Requirements

```
python-telegram-bot
sqlite3
google-generativeai
requests
openai # For DeepSeek support
```

## Installation

1. Clone the repository
2. Install the requirements: `pip install -r requirements.txt`
3. Set the required environment variables
4. Run the bot: `python bot.py`

## Natural Language Support

The bot `supports natural language commands when mentioned. For example:
- "@DebtBot add debt 50k to @toan for coffee"
- "@DebtBot split 90k between me, @quy and @tuan"
- "@DebtBot how much do I owe?"

The bot will analyze the intent using the configured AI model and execute the appropriate command. The natural language processing includes:

- Pattern matching for common commands
- AI-powered intent recognition for complex requests
- Conversational responses for non-debt-related questions
- Vietnamese language support with cultural references

## Data Backup

Admins can use the `/backup` command to generate and receive a database backup file via Telegram. This file can later be placed in the bot's directory for restoration.

## Security and Privacy

The bot separates data by chat_id, ensuring that debt information from one group doesn't appear in another. Admin commands are restricted to authorized user IDs only.
