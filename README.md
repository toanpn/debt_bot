# Debt Bot

A Telegram bot for tracking debts among friends and groups.

## Features

- 💰 Track debts between users
- 📊 View debt summaries and balances
- 📝 Add notes to debt records
- 🧾 View transaction history
- 👥 Divide expenses among multiple users
- 📱 Set and display QR codes for payments
- 🔄 Clear debts with other users
- 👤 Set custom display names

## Commands

- `/adddebt @username amount [note]` - Add a debt record
- `/summary [@username]` - View debt summary for yourself or another user
- `/clear @username` - Clear all debts with a specific user
- `/setname display_name` - Set your display name
- `/history [number]` - View recent transaction history (optional: specify number of records)
- `/groupsummary` - View summary of all debts in the group
- `/divide amount users [note]` - Split an expense among multiple users
- `/setqr url` - Set your payment QR code
- `/getqr @username` - Get the QR code of a user
- `/help` - Display help information

## Setup

The bot uses SQLite for data storage with the following tables:
- `debts` - Stores debt records
- `name_mappings` - Maps usernames to display names
- `qr_codes` - Stores payment QR codes

## Requirements

- Python 3
- python-telegram-bot
- sqlite3

## Usage

Run the bot with:

```
python bot.py
```


Make sure to set up your Telegram bot token before running.
