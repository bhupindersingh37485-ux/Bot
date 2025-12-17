import sqlite3
import smtplib
import csv
import os
import asyncio
import random
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    MessageHandler, filters, ConversationHandler
)

# --- CONFIGURATION ---
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_ID = 123456789 
DB = "data.db"
UPLOAD_DIR = "uploads/"

# Global flags
SENDING_ACTIVE = False 

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

# States for Conversation
EMAIL, PASSWORD, SUBJECT, MESSAGE_BODY = range(4)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # Changed 'session' to 'accounts' to support multiple logins
    cur.execute("CREATE TABLE IF NOT EXISTS accounts (email TEXT PRIMARY KEY, password TEXT, provider TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS mail (subject TEXT, message TEXT, attachment_path TEXT)")
    conn.commit()
    conn.close()

init_db()

# --- HELPER FUNCTIONS ---

# 1. Spin Syntax Processor (The Anti-Spam Magic)
def process_spintax(text):
    # Finds patterns like {word1|word2|word3} and picks one randomly
    pattern = r'\{([^{}]+)\}'
    while True:
        match = re.search(pattern, text)
        if not match:
            break
        options = match.group(1).split('|')
        choice = random.choice(options)
        text = text[:match.start()] + choice + text[match.end():]
    return text

def get_accounts():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT email, password, provider FROM accounts")
    rows = cur.fetchall()
    conn.close()
    return rows

def smtp_connect(email, password, provider):
    host = "smtp.gmail.com" if provider == "gmail" else "smtp.office365.com"
    server = smtplib.SMTP(host, 587)
    server.starttls()
    server.login(email, password)
    return server

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    accounts = get_accounts()
    acc_count = len(accounts)
    
    await update.message.reply_text(
        f"🤖 **ULTRA-MAILER BOT v3**\n"
        f"✅ Active Accounts: {acc_count}\n\n"
        "1️⃣ /add_account - Add Gmail/Outlook (Unlimited)\n"
        "2️⃣ /clear_accounts - Remove all accounts\n"
        "3️⃣ /set_content - Set Subject & HTML Body\n"
        "4️⃣ /attach - Upload File\n"
        "5️⃣ /upload_list - Upload CSV\n"
        "6️⃣ /send - Start Rotation Sending\n"
        "🛑 /stop - Emergency Stop\n\n"
        "💡 *Tip:* Use `{Hi|Hello}` in message for spam protection!",
        parse_mode='Markdown'
    )

# --- LOGIN (MULTI-ACCOUNT SUPPORT) ---
async def start_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("📧 Send the EMAIL address to add:")
    return EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["email"] = update.message.text
    await update.message.reply_text("🔑 Send APP PASSWORD:")
    return PASSWORD

async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = context.user_data["email"]
    password = update.message.text
    
    # Auto-detect provider
    provider = "gmail" if "gmail" in email else "outlook"
    
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO accounts VALUES (?,?,?)", (email, password, provider))
        conn.commit()
        await update.message.reply_text(f"✅ Added {email} to rotation!")
    except sqlite3.IntegrityError:
        await update.message.reply_text("⚠️ Account already exists.")
    finally:
        conn.close()
    
    return ConversationHandler.END

async def clear_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM accounts")
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑 All accounts removed.")

# --- CONTENT SETUP ---
async def start_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("✉️ Send SUBJECT (SpinTax supported e.g. `{Hot|New} Offer`):")
    return SUBJECT

async def get_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["subject"] = update.message.text
    await update.message.reply_text("📝 Send HTML MESSAGE (SpinTax supported `{Hi|Hello}`):")
    return MESSAGE_BODY

async def get_message_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM mail")
    # Store new content, keep attachment null for now
    cur.execute("INSERT INTO mail VALUES (?,?,?)", (context.user_data["subject"], update.message.text, None))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Content Saved with Spin Syntax support.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# --- ATTACHMENT & CSV ---
async def save_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    
    if update.message.document:
        file_obj = await update.message.document.get_file()
        fname = update.message.document.file_name
    elif update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
        fname = "image.jpg"
    else:
        return

    path = f"{UPLOAD_DIR}{fname}"
    await file_obj.download_to_drive(path)
    
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE mail SET attachment_path = ?", (path,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"📎 Attached: {fname}")

async def save_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    file = await update.message.document.get_file()
    await file.download_to_drive(f"{UPLOAD_DIR}emails.csv")
    await update.message.reply_text("📂 List Uploaded.")

# --- ENGINE ---
async def stop_sending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SENDING_ACTIVE
    SENDING_ACTIVE = False
    await update.message.reply_text("🛑 Stopping...")

async def send_emails(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SENDING_ACTIVE
    if update.effective_user.id != ADMIN_ID: return

    accounts = get_accounts()
    if not accounts: return await update.message.reply_text("❌ No accounts added!")

    conn = sqlite3.connect(DB)
    mail_data = conn.execute("SELECT subject, message, attachment_path FROM mail").fetchone()
    conn.close()
    
    if not mail_data: return await update.message.reply_text("❌ No content set!")
    if not os.path.exists(f"{UPLOAD_DIR}emails.csv"): return await update.message.reply_text("❌ No CSV found!")

    subject_raw, body_raw, attach_path = mail_data
    
    SENDING_ACTIVE = True
    report = ["--- EMAIL REPORT ---"]
    
    # Prepare SMTP connections for all accounts
    active_servers = []
    for email, pwd, prov in accounts:
        try:
            srv = smtp_connect(email, pwd, prov)
            active_servers.append({"server": srv, "email": email})
        except:
            report.append(f"⚠️ Failed to login: {email}")

    if not active_servers:
        return await update.message.reply_text("❌ Could not login to ANY accounts.")

    await update.message.reply_text(f"🚀 Started with {len(active_servers)} sender accounts.")

    server_index = 0
    with open(f"{UPLOAD_DIR}emails.csv", 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            if not SENDING_ACTIVE: break

            # 1. ROTATION LOGIC
            current_sender = active_servers[server_index]
            server_index = (server_index + 1) % len(active_servers) # Rotate to next
            
            # 2. SPIN SYNTAX LOGIC
            final_subject = process_spintax(subject_raw)
            final_body = process_spintax(body_raw).format(**row) # Fill {name}

            try:
                msg = MIMEMultipart()
                msg['From'] = current_sender["email"]
                msg['To'] = row['email']
                msg['Subject'] = final_subject
                
                msg.attach(MIMEText(final_body, 'html'))
                
                if attach_path and os.path.exists(attach_path):
                    with open(attach_path, "rb") as att:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(att.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(attach_path)}")
                    msg.attach(part)

                current_sender["server"].send_message(msg)
                print(f"Sent to {row['email']} via {current_sender['email']}")
                report.append(f"✅ {row['email']} (via {current_sender['email']})")
                
                await asyncio.sleep(random.randint(10, 30)) # Random delay looks more human

            except Exception as e:
                print(f"Fail: {e}")
                report.append(f"❌ {row['email']} - Error: {e}")
                # Try to reconnect this specific server if it died
                try:
                    current_sender["server"] = smtp_connect(current_sender["email"], accounts[server_index-1][1], accounts[server_index-1][2])
                except: pass

    # Cleanup
    for s in active_servers: s["server"].quit()
    
    # Send Report File
    with open(f"{UPLOAD_DIR}report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        
    await update.message.reply_document(document=open(f"{UPLOAD_DIR}report.txt", "rb"), caption="📊 Campaign Finished.")

# --- MAIN ---
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('clear_accounts', clear_accounts))
    app.add_handler(CommandHandler('attach', save_attachment))
    app.add_handler(CommandHandler('upload_list', save_csv))
    app.add_handler(CommandHandler('stop', stop_sending))
    app.add_handler(CommandHandler('send', send_emails))
    
    # Generic file handler
    app.add_handler(MessageHandler(filters.Document.MimeType("text/csv"), save_csv))
    app.add_handler(MessageHandler(filters.Document.ALL, save_attachment))

    # Conversation Handlers
    login_conv = ConversationHandler(
        entry_points=[CommandHandler('add_account', start_login)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    content_conv = ConversationHandler(
        entry_points=[CommandHandler('set_content', start_content)],
        states={
            SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_subject)],
            MESSAGE_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_message_body)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    app.add_handler(login_conv)
    app.add_handler(content_conv)

    print("Ultra-Mailer Running...")
    app.run_polling()
  
