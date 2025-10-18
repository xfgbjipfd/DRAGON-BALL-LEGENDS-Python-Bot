import telegram
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, ContextTypes, CallbackQueryHandler
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto, error
import random
from datetime import datetime, timedelta
import re 
import sqlite3
import os
import threading
import time

# =========================================================
#             الإعدادات والمتغيرات الأساسية
# =========================================================

# توكن البوت
BOT_TOKEN = "7596707301:AAE15SiMcFOSA5_BCNB28Pk5YPuop0x-yhE"

# ID المطور والقناة
DEVELOPER_ID = 6837667746 
POST_CHANNEL_ID = -1002851165871
BOT_USERNAME = "Genshin4BOT"
DEVELOPER_CHAT_URL = f"tg://user?id={DEVELOPER_ID}"
IMAGE_URL = "https://e.top4top.io/p_35771ntws0.jpg" # صورة البوت الرئيسية

# 📌 ملف قاعدة البيانات
DB_NAME = 'bot_database.db'

# القواميس المؤقتة (لبيانات الذاكرة التي لا تحتاج تخزين دائم)
pending_republish = {} 

REPUBLISH_DELAY_HOURS = 6 
REFERRAL_BONUS = 0.02 

# حالات محادثة أوامر المطور (إضافة/خصم مبلغ)
GETTING_ID, GETTING_AMOUNT_ADD, GETTING_AMOUNT_DEDUCT = range(3)

# حالات محادثة أمر "نشر حساب"
(
    GET_POST_PHOTO, 
    GET_POST_DETAILS, 
    GET_POST_PRICE, 
    GET_POST_SELLER_ID,
    AWAIT_POST_CONFIRMATION
) = range(3, 8) 

# حالات محادثة الفلترة
GET_FILTER_PRICE = 8 

# حالات محادثة أمر "اضف خدمة"
(
    GET_SERVICE_NAME,
    GET_SERVICE_PHOTO,
    GET_SERVICE_DETAILS,
    GET_SERVICE_PRICE,
    AWAIT_SERVICE_CONFIRMATION
) = range(9, 14)

# حالات محادثة أمر "اضف شحن"
(
    GET_CHARGE_BUTTON_NAME,
    GET_CHARGE_PHOTO,
    GET_CHARGE_DETAILS,
    GET_CHARGE_PRICE,
    AWAIT_CHARGE_CONFIRMATION
) = range(14, 19)

# حالات محادثة أوامر جديدة (حذف / كشف)
AWAITING_DELETE_INPUT = 19
AWAITING_REVEAL_ID = 20


# =========================================================
#            توابع إدارة قاعدة البيانات (SQLite)
# =========================================================

def db_connect():
    """ينشئ اتصالاً بقاعدة البيانات."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def db_initialize():
    """يقوم بإنشاء الجداول إذا لم تكن موجودة."""
    conn = db_connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0.00,
            referred_by INTEGER
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            photo_id TEXT NOT NULL,
            details TEXT,
            price REAL NOT NULL,
            seller_id INTEGER NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id TEXT PRIMARY KEY,
            name TEXT,
            photo_id TEXT NOT NULL,
            details TEXT,
            price REAL NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS charges (
            id TEXT PRIMARY KEY,
            button_name TEXT,
            photo_id TEXT NOT NULL,
            details TEXT,
            price REAL NOT NULL
        )
    """)
    
    conn.commit()
    conn.close()

# ---------------------------------------------------------
#       توابع الرصيد
# ---------------------------------------------------------

def db_get_balance(user_id):
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result['balance'] if result else 0.00

def db_set_balance(user_id, amount):
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, balance) VALUES (?, ?) "
                   "ON CONFLICT(user_id) DO UPDATE SET balance=?", 
                   (user_id, round(amount, 2), round(amount, 2)))
    conn.commit()
    conn.close()
    return round(amount, 2)
    
def db_get_or_create_user(user_id):
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

# ---------------------------------------------------------
#       توابع استرداد البيانات
# ---------------------------------------------------------

def db_get_all_accounts():
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM accounts")
    rows = cursor.fetchall()
    conn.close()
    return {row['id']: dict(row) for row in rows}

def db_get_all_services():
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM services")
    rows = cursor.fetchall()
    conn.close()
    return {row['id']: dict(row) for row in rows}

def db_get_all_charges():
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM charges")
    rows = cursor.fetchall()
    conn.close()
    return {row['id']: dict(row) for row in rows}

# =========================================================
#            توابع مساعدة (محدثة لاستخدام DB)
# =========================================================

def get_balance(user_id):
    db_get_or_create_user(user_id)
    return db_get_balance(user_id)

def set_balance(user_id, amount):
    return db_set_balance(user_id, amount)

def get_accounts_dict():
    return db_get_all_accounts()

def get_services_dict():
    return db_get_all_services()
    
def get_charges_dict():
    return db_get_all_charges()

def generate_transaction_id():
    return str(random.randint(100000, 999999))

def generate_account_link(account_id):
    return f"https://t.me/{BOT_USERNAME}?start=ACCOUNT_{account_id}"

def generate_service_link(service_id):
    return f"https://t.me/{BOT_USERNAME}?start=SERVICE_{service_id}"

def generate_charge_link(charge_id):
    return f"https://t.me/{BOT_USERNAME}?start=CHARGE_{charge_id}"

# 📌 تابع النبضات الحية
def send_heartbeat_threaded(bot):
    """يرسل نبضة حية للمطور كل 4 دقائق."""
    while True:
        time.sleep(240) 
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            bot.send_message(
                DEVELOPER_ID,
                f"🟢 نبضات حية: البوت لا يزال نشطًا. ({now})",
                disable_notification=True 
            )
        except Exception as e:
            print(f"⚠️ فشل إرسال النبضة الحية في Thread: {e}")

# 📌 تابع لتشغيل Thread النبضات الحية
def start_heartbeat_thread(application):
    bot_instance = application.bot
    
    thread = threading.Thread(target=send_heartbeat_threaded, args=(bot_instance,))
    thread.daemon = True 
    thread.start()
    print("بدأ تشغيل خيط النبضات الحية.")


async def republish_account_job(context: ContextTypes.DEFAULT_TYPE):
    account_id = context.job.data['account_id']
    
    if account_id in pending_republish:
        account_data = pending_republish.pop(account_id)['account_data']
        
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO accounts (id, photo_id, details, price, seller_id) 
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, account_data['photo_id'], account_data['details'], account_data['price'], account_data['seller_id']))
        conn.commit()
        conn.close()
        
        await context.bot.send_message(
            DEVELOPER_ID,
            f"🔔 **إعادة نشر تلقائي:**\nتمت إعادة إتاحة الحساب `{account_id}` (للبائع {account_data['seller_id']}) بعد فترة التأجيل.",
            parse_mode='Markdown'
        )

async def process_referral(referrer_id, new_user_id, context: ContextTypes.DEFAULT_TYPE):
    
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ? AND referred_by IS NOT NULL", (new_user_id,))
    is_counted = cursor.fetchone()
    
    if not is_counted:
        current_balance = get_balance(referrer_id)
        new_balance = set_balance(referrer_id, current_balance + REFERRAL_BONUS)
        
        cursor.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, new_user_id))
        conn.commit()
        
        try:
            notification_text = (
                f"🥳 تهانينا! انضم مستخدم جديد عن طريق رابط الإحالة الخاص بك. "
                f"تمت إضافة **{REFERRAL_BONUS:.2f}$** إلى رصيدك. "
                f"رصيدك الحالي: {new_balance:.2f}$."
            )
            await context.bot.send_message(
                chat_id=referrer_id,
                text=notification_text
            )
        except telegram.error.BadRequest:
            print(f"فشل إرسال إشعار المكافأة للمحيل: {referrer_id}")
    
    conn.close()

# =========================================================
#          توابع واجهة المستخدم والمعالجات
# =========================================================

# --- Handlers: Start, Main Menu & Navigation ---
async def get_main_keyboard(user_id):
    current_balance = get_balance(user_id)
    balance_text = f"رصيدك : {current_balance:.2f}$"
    
    keyboard = [
        [InlineKeyboardButton(balance_text, callback_data='show_balance')],
        [InlineKeyboardButton("الحسابات", callback_data='show_available_accounts'), 
         InlineKeyboardButton("الخدمات", callback_data='show_services_menu')], 
        [InlineKeyboardButton("نشر حساب", url=DEVELOPER_CHAT_URL)], 
        [InlineKeyboardButton("سحب/تعبئة الرصيد", url=DEVELOPER_CHAT_URL)],
        [InlineKeyboardButton("مشاركة", callback_data='show_referral')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_get_or_create_user(user_id) 
    
    # معالجة الروابط المشتركة
    if context.args:
        arg = context.args[0]
        if arg.startswith('CHARGE_') and arg.replace('CHARGE_', '') in get_charges_dict():
            return await view_charge(update, context, arg.replace('CHARGE_', ''))
        elif arg.startswith('SERVICE_') and arg.replace('SERVICE_', '') in get_services_dict():
            return await view_service(update, context, arg.replace('SERVICE_', ''))
        elif arg.startswith('ACCOUNT_') and arg.replace('ACCOUNT_', '') in get_accounts_dict():
            return await view_account(update, context, arg.replace('ACCOUNT_', ''))
        else:
            try:
                referrer_id = int(arg)
                if referrer_id != user_id:
                    await process_referral(referrer_id, user_id, context)
            except ValueError:
                pass
    
    reply_markup = await get_main_keyboard(user_id)
    WELCOME_MESSAGE = "متجر Mini متجر متخصص ببيع وشراء حسابات وخدمات قنشن تصفح أفضل الحسابات، واستفد من خدمات الشحن الآمنة والأسعار التنافسية. 📲"

    try:
        await update.message.reply_photo(
            photo=IMAGE_URL,
            caption=WELCOME_MESSAGE,
            reply_markup=reply_markup
        )
    except Exception as e:
        await update.message.reply_text("عذراً، حدث خطأ في إرسال رسالة الترحيب.")

async def referral_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    user_id = query.from_user.id
    
    referral_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    num_referrals = cursor.fetchone()[0]
    conn.close()
    
    details_text = (
        "**تصنيف أكثر مستخدمين أحالة:**\n\n"
        "ـ🥇 `7622341875` \n"
        "ـ🥈 `7342200121`\n"
        "ـ🥉 `5438472167`\n"
        
        "ـــــــــ\n"
        "قم بدعوة الأصدقاء واكسب رصيداً! عند انضمام مستخدم جديد عبر رابطك، ستحصل على مكافأة.\n\n"
        f"💸 ـ مكافأة الإحالة: **{REFERRAL_BONUS:.2f}$** \n\n"
        f"📲 ـ عدد الإحالات الناجحة: **{num_referrals}**\n\n"
        "اضغط على زر مشاركة الرابط لإرسال رابطك الخاص و مشاركة مع اصدقائك. 👇"
    )

    new_keyboard = [
        [InlineKeyboardButton("🔗 مشاركة الرابط", url=f"https://t.me/share/url?url={referral_link}&text=انضم%20إلى%20البوت%20عبر%20رابطي%20واحصل%20على%20مكافأة!")],
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]
    reply_markup = InlineKeyboardMarkup(new_keyboard)

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(media=IMAGE_URL, caption=details_text, parse_mode='Markdown'),
            reply_markup=reply_markup
        )
    except telegram.error.BadRequest:
        pass

async def go_back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    reply_markup = await get_main_keyboard(user_id)
    WELCOME_MESSAGE = "متجر Mini متجر متخصص ببيع وشراء حسابات وخدمات قنشن تصفح أفضل الحسابات، واستفد من خدمات الشحن الآمنة والأسعار التنافسية. 📲"

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(media=IMAGE_URL, caption=WELCOME_MESSAGE, parse_mode='Markdown'),
            reply_markup=reply_markup
        )
    except telegram.error.BadRequest as e:
        try:
            await query.edit_message_caption(
                caption=WELCOME_MESSAGE,
                reply_markup=reply_markup
            )
        except telegram.error.BadRequest:
            pass

# --- Handlers: Dev Commands (Balance, Delete, Reveal) ---
async def add_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END
    await update.message.reply_text("💰 **أضف مبلغ**\nالرجاء إرسال ID المستخدم الذي تريد إضافة المبلغ إليه:", parse_mode='Markdown')
    context.user_data['operation'] = 'add'
    return GETTING_ID

async def deduct_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END
    await update.message.reply_text("➖ **خصم مبلغ**\nالرجاء إرسال ID المستخدم الذي تريد خصم المبلغ منه:", parse_mode='Markdown')
    context.user_data['operation'] = 'deduct'
    return GETTING_ID

async def start_delete_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END
    await update.message.reply_text("🗑️ **حذف عنصر (حساب/خدمة/عرض)**\nالرجاء إرسال **ID** أو **اسم** الحساب/الخدمة/العرض الذي تريد حذفه:", parse_mode='Markdown')
    return AWAITING_DELETE_INPUT

async def process_delete_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item_input = update.message.text.strip()
    
    conn = db_connect()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM accounts WHERE id = ?", (item_input,))
    deleted_rows_acc = cursor.rowcount

    cursor.execute("DELETE FROM services WHERE id = ? OR name = ?", (item_input, item_input))
    deleted_rows_srv = cursor.rowcount

    cursor.execute("DELETE FROM charges WHERE id = ? OR button_name = ?", (item_input, item_input))
    deleted_rows_chr = cursor.rowcount

    conn.commit()
    conn.close()
    
    total_deleted = deleted_rows_acc + deleted_rows_srv + deleted_rows_chr

    if total_deleted > 0:
        await update.message.reply_text(f"✅ تم حذف {total_deleted} عنصر بنجاح: `{item_input}`.", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ لم يتم العثور على حساب، خدمة، أو عرض شحن بهذا المعرف/الاسم. تأكد من الإدخال.", parse_mode='Markdown')
        return AWAITING_DELETE_INPUT
        
    return ConversationHandler.END

async def start_reveal_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END
    await update.message.reply_text("🔎 **كشف رصيد مستخدم**\nالرجاء إرسال **ID المستخدم** الذي تريد كشف رصيده:", parse_mode='Markdown')
    return AWAITING_REVEAL_ID

async def process_reveal_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id_input = update.message.text.strip()
    
    try:
        target_id = int(user_id_input)
        
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            balance = result['balance']
            await update.message.reply_text(f"💰 رصيد المستخدم `{target_id}` هو: **{balance:.2f}$**.", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ المستخدم `{target_id}` لم يتم تسجيله بعد أو لا يملك رصيدًا.", parse_mode='Markdown')
            
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون ID المستخدم رقماً صحيحاً.", parse_mode='Markdown')
        return AWAITING_REVEAL_ID

async def get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id_to_modify = int(update.message.text.strip())
        context.user_data['target_user_id'] = user_id_to_modify
        await update.message.reply_text(f"تم تحديد ID المستخدم: `{user_id_to_modify}`.\nالرجاء إرسال **قيمة المبلغ** (بالأرقام فقط) بالدولار $:", parse_mode='Markdown')
        if context.user_data['operation'] == 'add':
            return GETTING_AMOUNT_ADD
        else:
            return GETTING_AMOUNT_DEDUCT
    except ValueError:
        await update.message.reply_text("❌ ID المستخدم يجب أن يكون رقماً صحيحاً. حاول مرة أخرى.")
        return GETTING_ID 

async def process_amount_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = context.user_data.pop('target_user_id')
    try:
        amount_to_add = float(update.message.text.strip())
        if amount_to_add <= 0:
             await update.message.reply_text("❌ يجب أن يكون المبلغ أكبر من الصفر. حاول مرة أخرى.")
             return GETTING_AMOUNT_ADD
        current_balance = get_balance(target_user_id)
        new_balance = set_balance(target_user_id, current_balance + amount_to_add)
        await update.message.reply_text(f"✅ **تم إضافة المبلغ بنجاح!**\nالمستخدم: `{target_user_id}`\nالمبلغ المضاف: **{amount_to_add:.2f}$**\nالرصيد الجديد: **{new_balance:.2f}$**", parse_mode='Markdown')
        try:
            notification_text = f" تمت إضافة {amount_to_add:.2f}$ إلى رصيدك.✅"
            await context.bot.send_message(chat_id=target_user_id, text=notification_text)
        except telegram.error.BadRequest:
            await update.message.reply_text(f"⚠️ **تنبيه:** لم يتمكن البوت من إشعار المستخدم `{target_user_id}`. قد يكون حظر البوت.")
        return ConversationHandler.END 
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون المبلغ رقماً صحيحاً أو عشرياً (مثل 10.5). حاول مرة أخرى.")
        return GETTING_AMOUNT_ADD 

async def process_amount_deduct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user_id = context.user_data.pop('target_user_id')
    try:
        amount_to_deduct = float(update.message.text.strip())
        if amount_to_deduct <= 0:
             await update.message.reply_text("❌ يجب أن يكون المبلغ أكبر من الصفر. حاول مرة أخرى.")
             return GETTING_AMOUNT_DEDUCT
        current_balance = get_balance(target_user_id)
        new_balance = set_balance(target_user_id, max(0, current_balance - amount_to_deduct))
        await update.message.reply_text(f"✅ **تم خصم المبلغ بنجاح!**\nالمستخدم: `{target_user_id}`\nالمبلغ المخصوم: **{amount_to_deduct:.2f}$**\nالرصيد الجديد: **{new_balance:.2f}$**", parse_mode='Markdown')
        return ConversationHandler.END 
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون المبلغ رقماً صحيحاً أو عشرياً. حاول مرة أخرى.")
        return GETTING_AMOUNT_DEDUCT 

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == DEVELOPER_ID:
        await update.message.reply_text("تم إلغاء العملية.")
        context.user_data.clear()
        return ConversationHandler.END
    
    reply_markup = await get_main_keyboard(update.effective_user.id)
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=reply_markup)
    return ConversationHandler.END

# --- Handlers: Account Post, Filter, Purchase & Navigation ---
async def start_new_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END

    await update.message.reply_text("🖼️ **نشر حساب جديد**\n\nالرجاء إرسال **صورة واحدة** للحساب:", parse_mode='Markdown')
    context.user_data['post_data'] = {}
    return GET_POST_PHOTO

async def get_post_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['post_data']['photo_id'] = update.message.photo[-1].file_id
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📝 أحسنت. الآن، الرجاء إرسال **تفاصيل الحساب الكاملة** (نص وصفي):",
            parse_mode='Markdown'
        )
        return GET_POST_DETAILS

async def get_post_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['post_data']['details'] = update.message.text
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="💵 ممتاز. الآن، الرجاء إرسال **سعر الحساب** (بالأرقام فقط):",
            parse_mode='Markdown'
        )
        return GET_POST_PRICE

async def get_post_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
        context.user_data['post_data']['price'] = f"{price:.2f}$"
        
        await update.message.delete()
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="👤 **الخطوة الأخيرة:** الرجاء إرسال **ID/معرف المستخدم البائع** (رقم تيليجرام):",
            parse_mode='Markdown'
        )

        return GET_POST_SELLER_ID

    except ValueError:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً صحيحاً أو عشرياً موجباً. حاول مجدداً أو أرسل /cancel.")
        return GET_POST_SELLER_ID

async def get_post_seller_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        seller_id = int(update.message.text.strip())
        context.user_data['post_data']['seller_id'] = seller_id
        
        await update.message.delete()
        
        data = context.user_data['post_data']
        
        preview_caption = (
            f"✨ **معاينة النشر (للمطور)** ✨\n\n"
            f"**التفاصيل:**\n{data['details']}\n\n"
            f"**السعر:** {data['price']}\n"
            f"**ID البائع:** `{seller_id}`"
        )
        
        preview_keyboard = [
            [
                InlineKeyboardButton("✅ نشر الآن", callback_data='dev_publish_now'),
                InlineKeyboardButton("❌ إلغاء", callback_data='dev_cancel_post')
            ]
        ]
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=data['photo_id'],
            caption=preview_caption,
            reply_markup=InlineKeyboardMarkup(preview_keyboard),
            parse_mode='Markdown'
        )

        return AWAIT_POST_CONFIRMATION

    except ValueError:
        await update.message.reply_text("❌ ID المستخدم يجب أن يكون رقماً صحيحاً. حاول مجدداً أو أرسل /cancel.")
        return GET_POST_SELLER_ID

async def post_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = context.user_data['post_data']
    seller_id = data.get('seller_id')
    
    if query.data == 'dev_publish_now':
        
        account_id = f"ACC{generate_transaction_id()}" 
        price_float = float(data['price'].strip('$'))
        
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO accounts (id, photo_id, details, price, seller_id) 
            VALUES (?, ?, ?, ?, ?)
        """, (account_id, data['photo_id'], data['details'], price_float, seller_id))
        conn.commit()
        conn.close()

        final_caption = (
            f"**تفاصيل الحساب : Genshin impact**\n"
            f"{data['details']}" 
        )
        
        account_start_link = generate_account_link(account_id)
        
        channel_keyboard = [
            [InlineKeyboardButton(f"السعر: {price_float:.2f}$", callback_data='ignore_price')],
            [
                InlineKeyboardButton("✅ شراء الحساب", url=account_start_link),
                InlineKeyboardButton("🔗 مشاركة", url=f"https://t.me/share/url?url={account_start_link}&text=🔥%20شاهد%20هذا%20الحساب%20المميز%20للبيع%20على%20البوت!")
            ]
        ]
        
        try:
            await context.bot.send_photo(
                chat_id=POST_CHANNEL_ID,
                photo=data['photo_id'],
                caption=final_caption,
                reply_markup=InlineKeyboardMarkup(channel_keyboard),
                parse_mode='Markdown'
            )
            confirmation_message = "✅ **تم نشر الحساب بنجاح وإتاحته للشراء!**"
        except Exception as e:
            print(f"فشل النشر في القناة: {e}")
            confirmation_message = "⚠️ **تم إضافة الحساب للشراء لكن فشل النشر في القناة.**"

        if seller_id:
            try:
                await context.bot.send_message(
                    chat_id=seller_id,
                    text="✅ تم نشر حسابك بنجاح!"
                )
            except telegram.error.BadRequest:
                print(f"فشل إشعار البائع {seller_id} بنشر الحساب.")

    elif query.data == 'dev_cancel_post':
        confirmation_message = "❌ **تم إلغاء عملية النشر.**"
    
    await query.edit_message_caption(
        caption=confirmation_message,
        reply_markup=None,
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_new_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == DEVELOPER_ID:
        await update.message.reply_text("تم إلغاء عملية النشر.")
        context.user_data.clear()
        return ConversationHandler.END
    return ConversationHandler.END

async def filter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try:
        await context.bot.delete_message(
            chat_id=query.message.chat.id,
            message_id=query.message.message_id
        )
    except Exception:
        pass 
        
    sent_message = await context.bot.send_message(
        chat_id=query.message.chat.id,
        text="💰 **فلترة الحسابات حسب السعر**\n\nيرجئ إرسال **رقم السعر** (مثلاً: 10.5) لإظهار حسابات بأقل من السعر المحدد:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء الفلترة", callback_data='cancel_filter')]]),
        parse_mode='Markdown'
    )
    context.user_data['filter_message_id'] = sent_message.message_id
    
    return GET_FILTER_PRICE

async def get_filter_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    if update.message is None or update.message.text is None:
        return GET_FILTER_PRICE 

    try:
        max_price = float(update.message.text.strip())
        if max_price <= 0:
            raise ValueError
        
    except ValueError:
        await update.message.reply_text("❌ يرجى إرسال رقم سعر صحيح وموجب فقط. حاول مجدداً أو أرسل /cancel.")
        return GET_FILTER_PRICE
        
    try:
        await update.message.delete()
    except Exception:
        pass
        
    if 'filter_message_id' in context.user_data:
        try:
            await context.bot.delete_message(update.effective_chat.id, context.user_data.pop('filter_message_id'))
        except Exception:
            pass
        
    accounts = db_get_all_accounts()
    filtered_keys = [
        acc_id for acc_id, acc_data in accounts.items() 
        if acc_data['price'] < max_price
    ]
    
    context.user_data['filtered_accounts'] = filtered_keys
    
    
    if filtered_keys:
        context.user_data['current_account_index'] = 0
        first_account_id = filtered_keys[0]
        
        await view_account(update, context, first_account_id)
        
    else:
        await update.message.reply_text(
            f"❌ لا توجد حسابات بسعر أقل من {max_price:.2f}$",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
            ]),
            parse_mode='Markdown'
        )
        if 'filtered_accounts' in context.user_data:
            del context.user_data['filtered_accounts']
    
    return ConversationHandler.END


async def cancel_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    if 'filter_message_id' in context.user_data:
        try:
            await context.bot.delete_message(update.effective_chat.id, context.user_data.pop('filter_message_id'))
        except Exception:
            pass
            
    if 'filtered_accounts' in context.user_data:
        del context.user_data['filtered_accounts']
        
    if update.callback_query:
        query = update.callback_query
        await query.answer("تم إلغاء الفلترة.")
        return await go_back_to_start(update, context)
        
    await update.message.reply_text("تم إلغاء الفلترة والعودة للقائمة الرئيسية.")
    return ConversationHandler.END

async def get_account_list(context: ContextTypes.DEFAULT_TYPE):
    accounts = db_get_all_accounts()
    if 'filtered_accounts' in context.user_data:
        valid_keys = [key for key in context.user_data['filtered_accounts'] if key in accounts]
        return valid_keys
    return list(accounts.keys())

async def show_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if 'filtered_accounts' in context.user_data:
        del context.user_data['filtered_accounts']


    account_keys = await get_account_list(context)

    if not account_keys:
        caption = "❌ لا توجد حسابات متاحة حاليًا للشراء."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]])
        
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=IMAGE_URL, caption=caption, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except telegram.error.BadRequest:
             await context.bot.send_message(
                chat_id=query.message.chat.id,
                text=caption,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    else:
        first_account_id = account_keys[0]
        context.user_data['current_account_index'] = 0
        return await view_account(update, context, first_account_id)


async def view_account(update: Update, context: ContextTypes.DEFAULT_TYPE, account_id: str):
    query = update.callback_query
    
    update_obj = update if update.message else query

    accounts = db_get_all_accounts()
    account = accounts.get(account_id)
    
    if not account:
        if update_obj:
            try:
                await update_obj.message.reply_text("❌ عذراً، لم يتم العثور على هذا الحساب.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]]))
            except Exception:
                 await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=query.message.message_id if query else None,
                    caption="❌ عذراً، لم يتم العثور على هذا الحساب.", 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]])
                )
        return


    price_float = account['price']
    
    caption = (
        f"**تفاصيل الحساب : Genshin impact**\n"
        f"{account['details']}\n"
    )
    
    account_keys = await get_account_list(context)
    current_index = context.user_data.get('current_account_index', 0)
    total_accounts = len(account_keys)
    
    nav_buttons = []
    
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f'nav_prev_{current_index}'))
    
    if current_index < total_accounts - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f'nav_next_{current_index}'))
    elif total_accounts > 0 and current_index == total_accounts - 1:
        nav_buttons.append(InlineKeyboardButton("التالي 🔄", callback_data='nav_random'))

    
    # 📌 التعديل هنا: زر المشاركة فوق زر الفلترة
    keyboard = [
        [InlineKeyboardButton(f"السعر: {price_float:.2f}$", callback_data='ignore')],
        [InlineKeyboardButton("✅ شراء", callback_data=f'buy_{account_id}')],
        nav_buttons,
        [InlineKeyboardButton("🔗 مشاركة الحساب", url=f"https://t.me/share/url?url={generate_account_link(account_id)}&text=🔥%20شاهد%20هذا%20الحساب%20المميز%20للبيع%20على%20البوت!")],
        [InlineKeyboardButton("🔍 فلترة الحسابات", callback_data='filter_start')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]

    try:
        if query and hasattr(query, 'edit_message_media'):
             await query.edit_message_media(
                media=InputMediaPhoto(media=account['photo_id'], caption=caption, parse_mode='Markdown'),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.message:
             await update.message.reply_photo(
                photo=account['photo_id'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
             await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=account['photo_id'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
    except telegram.error.BadRequest as e:
        if query:
            if "message can't be edited" in str(e) or "Message is not modified" in str(e):
                await query.answer() 
                await context.bot.send_photo(
                    chat_id=query.message.chat.id,
                    photo=account['photo_id'],
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                 await query.answer(f"حدث خطأ: {e}")


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    account_keys = await get_account_list(context)
    if not account_keys:
        return await show_accounts(update, context)

    current_index = context.user_data.get('current_account_index', 0)
    total_accounts = len(account_keys)
    new_index = current_index

    if query.data.startswith('nav_next_'):
        new_index = current_index + 1
    elif query.data.startswith('nav_prev_'):
        new_index = current_index - 1
    elif query.data == 'nav_random':
        available_indices = [i for i in range(total_accounts) if i != current_index]
        if available_indices:
            new_index = random.choice(available_indices)
        else:
            return

    if 0 <= new_index < total_accounts:
        context.user_data['current_account_index'] = new_index
        next_account_id = account_keys[new_index]
        return await view_account(update, context, next_account_id)
    else:
        return await view_account(update, context, account_keys[current_index])


async def handle_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    account_id = query.data.split('_')[1]
    
    accounts = db_get_all_accounts()
    account = accounts.get(account_id)
    
    if not account:
        await query.answer("❌ عذراً، هذا الحساب لم يعد متاحاً.", show_alert=True)
        return

    price = float(account['price'])
    current_balance = get_balance(user_id)
    seller_id = account.get('seller_id') 

    if current_balance < price:
        return await query.answer("❌ رصيدك غير كافٍ لإتمام عملية الشراء.", show_alert=True)

    set_balance(user_id, current_balance - price)
    transaction_id = generate_transaction_id()
    purchase_date = datetime.now().strftime("%Y/%m/%d")

    # حذف الحساب من قاعدة البيانات
    conn = db_connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    
    republish_time = datetime.now() + timedelta(hours=REPUBLISH_DELAY_HOURS)
    pending_republish[account_id] = {'account_data': account, 'republish_time': republish_time}
    
    context.application.job_queue.run_once(
        republish_account_job, 
        when=timedelta(hours=REPUBLISH_DELAY_HOURS),
        data={'account_id': account_id},
        name=f"republish_{account_id}"
    )

    success_caption = (
        "✅ **تم شراء الحساب بنجاح!**\n\n"
        f"رقم المعاملة: `{transaction_id}`\n"
        f"تاريخ الشراء: {purchase_date}\n\n"
        "يرجئ تواصل مع الدعم للأستلام الحساب"
    )
    
    if seller_id and seller_id != user_id:
        seller_notification_text = (
            "✅ **تم شراء حسابك بنجاح!**\n"
            f"رقم المعاملة: `{transaction_id}`\n\n"
            "يرجئ التسليم الحساب للدعم."
        )
        seller_keyboard = [
            [InlineKeyboardButton("💬 تواصل مع الدعم", url=DEVELOPER_CHAT_URL)] 
        ]
        try:
            await context.bot.send_message(
                chat_id=seller_id,
                text=seller_notification_text,
                reply_markup=InlineKeyboardMarkup(seller_keyboard),
                parse_mode='Markdown'
            )
        except telegram.error.BadRequest as e:
            await context.bot.send_message(DEVELOPER_ID, f"⚠️ فشل إشعار البائع {seller_id} بخصوص المعاملة {transaction_id}. الخطأ: {e}")


    receipt_keyboard = [
        [InlineKeyboardButton("💬 تواصل مع الدعم", url=DEVELOPER_CHAT_URL)],
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]
    try:
        await query.edit_message_caption(
            caption=success_caption,
            reply_markup=InlineKeyboardMarkup(receipt_keyboard),
            parse_mode='Markdown'
        )
    except telegram.error.BadRequest:
        await query.message.reply_text(success_caption, reply_markup=InlineKeyboardMarkup(receipt_keyboard), parse_mode='Markdown')
    
    await query.answer()

# --- Handlers: Service & Charge Views ---
async def show_services_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    caption = "عروض شحن كرستالات فورية، وجميع خدمات التفريم اليدوية بأمان تام وأفضل الأسعار. ⭐"
    
    keyboard = [
        [InlineKeyboardButton("عروض الخدمات", callback_data='show_service_offers')], 
        [InlineKeyboardButton("شحن كرستالات", callback_data='show_charge_offers')], 
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.edit_message_media(
            media=InputMediaPhoto(media=IMAGE_URL, caption=caption, parse_mode='Markdown'),
            reply_markup=reply_markup
        )
    except telegram.error.BadRequest:
        pass

async def get_service_list(context: ContextTypes.DEFAULT_TYPE):
    services = db_get_all_services()
    return list(services.keys())

async def show_service_offers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    service_keys = await get_service_list(context)

    if not service_keys:
        caption = "❌ لا توجد خدمات متاحة حاليًا."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')]])
        
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=IMAGE_URL, caption=caption, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except telegram.error.BadRequest:
             pass 

        return
    else:
        first_service_id = service_keys[0]
        context.user_data['current_service_index'] = 0
        return await view_service(update, context, first_service_id)
        
    

async def view_service(update: Update, context: ContextTypes.DEFAULT_TYPE, service_id: str):
    query = update.callback_query
    update_obj = update if update.message else query

    services = db_get_all_services()
    service = services.get(service_id)
    
    if not service:
        caption = "❌ عذراً، لم يتم العثور على هذه الخدمة."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')]])
        
        try:
             await update_obj.message.reply_text(caption, reply_markup=reply_markup)
        except Exception:
            pass
        return

    price_float = service['price']
    service_name = service.get('name', 'خدمة غير معروفة')
    
    caption = (
        f"**تفاصيل الخدمة : {service_name}**\n\n"
        f"**{service['details']}**\n"
    )
    
    service_keys = await get_service_list(context)
    current_index = context.user_data.get('current_service_index', 0)
    total_services = len(service_keys)

    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f'service_prev_{current_index}'))
    if current_index < total_services - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f'service_next_{current_index}'))

    keyboard = [
        [InlineKeyboardButton(f"السعر: {price_float:.2f}$", callback_data='ignore_service_price')],
        [InlineKeyboardButton("✅ طلب الخدمة", callback_data=f'buy_service_{service_id}')],
        nav_buttons,
        [InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')] 
    ]

    try:
        if query and hasattr(query, 'edit_message_media'):
             await query.edit_message_media(
                media=InputMediaPhoto(media=service['photo_id'], caption=caption, parse_mode='Markdown'),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.message:
             await update.message.reply_photo(
                photo=service['photo_id'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    except telegram.error.BadRequest as e:
        await context.bot.send_message(update.effective_chat.id, f"❌ حدث خطأ: {e}", parse_mode='Markdown')

async def handle_service_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    service_keys = await get_service_list(context)
    if not service_keys:
        return await show_service_offers(update, context)

    current_index = context.user_data.get('current_service_index', 0)
    total_services = len(service_keys)
    new_index = current_index

    if query.data.startswith('service_next_'):
        new_index = current_index + 1
    elif query.data.startswith('service_prev_'):
        new_index = current_index - 1

    if 0 <= new_index < total_services:
        context.user_data['current_service_index'] = new_index
        next_service_id = service_keys[new_index]
        return await view_service(update, context, next_service_id)
    else:
        return await view_service(update, context, service_keys[current_index])

async def handle_service_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    service_id = query.data.split('_')[2]
    
    services = db_get_all_services()
    service = services.get(service_id)
    
    if not service:
        await query.answer("❌ عذراً، هذه الخدمة لم تعد متاحة.", show_alert=True)
        return

    price = float(service['price'])
    current_balance = get_balance(user_id)

    if current_balance < price:
        return await query.answer("❌ رصيدك غير كافٍ لطلب الخدمة.", show_alert=True)

    set_balance(user_id, current_balance - price)
    transaction_id = generate_transaction_id()
    purchase_date = datetime.now().strftime("%Y/%m/%d")

    await context.bot.send_message(
        DEVELOPER_ID,
        f"🔔 **طلب خدمة جديد!**\n"
        f"المستخدم: `{user_id}`\n"
        f"الخدمة: `{service_id}` ({service.get('name', 'N/A')})\n"
        f"المبلغ المدفوع: **{price:.2f}$**\n"
        f"رقم المعاملة: `{transaction_id}`",
        parse_mode='Markdown'
    )

    success_caption = (
        "✅ **تم طلب الخدمة بنجاح!**\n\n"
        f"رقم المعاملة: `{transaction_id}`\n"
        f"تاريخ الطلب: {purchase_date}\n\n"
        "يرجئ التواصل مع الدعم لتنفيذ الخدمة المطلوبة."
    )
    
    receipt_keyboard = [
        [InlineKeyboardButton("💬 تواصل مع الدعم", url=DEVELOPER_CHAT_URL)],
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]
    try:
        await query.edit_message_caption(
            caption=success_caption,
            reply_markup=InlineKeyboardMarkup(receipt_keyboard),
            parse_mode='Markdown'
        )
    except telegram.error.BadRequest:
        await query.message.reply_text(success_caption, reply_markup=InlineKeyboardMarkup(receipt_keyboard), parse_mode='Markdown')
    
    await query.answer()

async def get_charge_list(context: ContextTypes.DEFAULT_TYPE):
    charges = db_get_all_charges()
    return list(charges.keys())

async def show_charge_offers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    charge_keys = await get_charge_list(context)

    if not charge_keys:
        caption = "❌ لا توجد عروض شحن متاحة حاليًا."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')]])
        
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(media=IMAGE_URL, caption=caption, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except telegram.error.BadRequest:
             pass 

        return
    else:
        first_charge_id = charge_keys[0]
        context.user_data['current_charge_index'] = 0
        return await view_charge(update, context, first_charge_id)

async def view_charge(update: Update, context: ContextTypes.DEFAULT_TYPE, charge_id: str):
    query = update.callback_query
    update_obj = update if update.message else query

    charges = db_get_all_charges()
    charge = charges.get(charge_id)
    
    if not charge:
        caption = "❌ عذراً، لم يتم العثور على هذا العرض."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')]])
        
        try:
             await update_obj.message.reply_text(caption, reply_markup=reply_markup)
        except Exception:
            pass
        return

    price_float = charge['price']
    button_name = charge.get('button_name', 'عرض شحن')
    
    caption = (
        f"**عرض الشحن : {button_name}**\n\n"
        f"**{charge['details']}**\n"
    )
    
    charge_keys = await get_charge_list(context)
    current_index = context.user_data.get('current_charge_index', 0)
    total_charges = len(charge_keys)

    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f'charge_prev_{current_index}'))
    if current_index < total_charges - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f'charge_next_{current_index}'))

    keyboard = [
        [InlineKeyboardButton(f"السعر: {price_float:.2f}$", callback_data='ignore_charge_price')],
        [InlineKeyboardButton("✅ طلب الشحن", callback_data=f'buy_charge_{charge_id}')],
        nav_buttons,
        [InlineKeyboardButton("🔙 رجوع", callback_data='show_services_menu')] 
    ]

    try:
        if query and hasattr(query, 'edit_message_media'):
             await query.edit_message_media(
                media=InputMediaPhoto(media=charge['photo_id'], caption=caption, parse_mode='Markdown'),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.message:
             await update.message.reply_photo(
                photo=charge['photo_id'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    except telegram.error.BadRequest as e:
        await context.bot.send_message(update.effective_chat.id, f"❌ حدث خطأ: {e}", parse_mode='Markdown')

async def handle_charge_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    charge_keys = await get_charge_list(context)
    if not charge_keys:
        return await show_charge_offers(update, context)

    current_index = context.user_data.get('current_charge_index', 0)
    total_charges = len(charge_keys)
    new_index = current_index

    if query.data.startswith('charge_next_'):
        new_index = current_index + 1
    elif query.data.startswith('charge_prev_'):
        new_index = current_index - 1

    if 0 <= new_index < total_charges:
        context.user_data['current_charge_index'] = new_index
        next_charge_id = charge_keys[new_index]
        return await view_charge(update, context, next_charge_id)
    else:
        return await view_charge(update, context, charge_keys[current_index])

async def handle_charge_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    charge_id = query.data.split('_')[2]
    
    charges = db_get_all_charges()
    charge = charges.get(charge_id)
    
    if not charge:
        await query.answer("❌ عذراً، هذا العرض لم يعد متاحاً.", show_alert=True)
        return

    price = float(charge['price'])
    current_balance = get_balance(user_id)

    if current_balance < price:
        return await query.answer("❌ رصيدك غير كافٍ لطلب الشحن.", show_alert=True)

    set_balance(user_id, current_balance - price)
    transaction_id = generate_transaction_id()
    purchase_date = datetime.now().strftime("%Y/%m/%d")

    await context.bot.send_message(
        DEVELOPER_ID,
        f"🔔 **طلب شحن جديد!**\n"
        f"المستخدم: `{user_id}`\n"
        f"العرض: `{charge_id}` ({charge.get('button_name', 'N/A')})\n"
        f"المبلغ المدفوع: **{price:.2f}$**\n"
        f"رقم المعاملة: `{transaction_id}`",
        parse_mode='Markdown'
    )

    success_caption = (
        "✅ **تم طلب الشحن بنجاح!**\n\n"
        f"رقم المعاملة: `{transaction_id}`\n"
        f"تاريخ الطلب: {purchase_date}\n\n"
        "يرجئ التواصل مع الدعم لتنفيذ عملية الشحن."
    )
    
    receipt_keyboard = [
        [InlineKeyboardButton("💬 تواصل مع الدعم", url=DEVELOPER_CHAT_URL)],
        [InlineKeyboardButton("🔙 رجوع", callback_data='go_back_to_start')]
    ]
    try:
        await query.edit_message_caption(
            caption=success_caption,
            reply_markup=InlineKeyboardMarkup(receipt_keyboard),
            parse_mode='Markdown'
        )
    except telegram.error.BadRequest:
        await query.message.reply_text(success_caption, reply_markup=InlineKeyboardMarkup(receipt_keyboard), parse_mode='Markdown')
    
    await query.answer()

# --- Handlers: Service Post ---
async def start_new_service_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END

    await update.message.reply_text("📝 **نشر خدمة جديدة**\n\nالرجاء إرسال **اسم الخدمة** (مثلاً: تنكيس 5 نجوم):", parse_mode='Markdown')
    context.user_data['service_data'] = {}
    return GET_SERVICE_NAME

async def get_service_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['service_data']['name'] = update.message.text.strip()
        try:
             await update.message.delete()
        except Exception:
             pass
             
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🖼️ أحسنت. الآن، الرجاء إرسال **صورة واحدة** للخدمة:",
            parse_mode='Markdown'
        )
        return GET_SERVICE_PHOTO
    else:
        await update.message.reply_text("❌ يرجى إرسال اسم الخدمة كنص. حاول مجدداً أو أرسل /cancel.")
        return GET_SERVICE_NAME

async def get_service_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['service_data']['photo_id'] = update.message.photo[-1].file_id
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📝 أحسنت. الآن، الرجاء إرسال **تفاصيل الخدمة الكاملة** (نص وصفي):",
            parse_mode='Markdown'
        )
        return GET_SERVICE_DETAILS
    else:
        await update.message.reply_text("❌ الرجاء إرسال صورة واحدة فقط. حاول مجدداً أو أرسل /cancel.")
        return GET_SERVICE_PHOTO

async def get_service_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['service_data']['details'] = update.message.text
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="💵 ممتاز. الآن، الرجاء إرسال **سعر الخدمة** (بالأرقام فقط):",
            parse_mode='Markdown'
        )
        return GET_SERVICE_PRICE
    else:
        await update.message.reply_text("❌ الرجاء إرسال التفاصيل كنص. حاول مجدداً أو أرسل /cancel.")
        return GET_SERVICE_DETAILS

async def get_service_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
        context.user_data['service_data']['price'] = f"{price:.2f}$"
        
        await update.message.delete()
        
        data = context.user_data['service_data']
        service_name = data['name']
        
        preview_caption = (
            f"✨ **معاينة الخدمة (للمطور)** ✨\n\n"
            f"**الاسم:** {service_name}\n"
            f"**التفاصيل:**\n{data['details']}\n\n"
            f"**السعر:** {data['price']}"
        )
        
        preview_keyboard = [
            [
                InlineKeyboardButton("✅ نشر الآن", callback_data='dev_publish_service_now'),
                InlineKeyboardButton("❌ إلغاء", callback_data='dev_cancel_service_post')
            ]
        ]
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=data['photo_id'],
            caption=preview_caption,
            reply_markup=InlineKeyboardMarkup(preview_keyboard),
            parse_mode='Markdown'
        )
        return AWAIT_SERVICE_CONFIRMATION

    except ValueError:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً صحيحاً أو عشرياً موجباً. حاول مجدداً أو أرسل /cancel.")
        return GET_SERVICE_PRICE
        
async def post_service_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = context.user_data['service_data']
    
    if query.data == 'dev_publish_service_now':
        
        service_id = f"SRV{generate_transaction_id()}" 
        
        # حفظ الخدمة في قاعدة البيانات
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO services (id, name, photo_id, details, price) 
            VALUES (?, ?, ?, ?, ?)
        """, (service_id, data['name'], data['photo_id'], data['details'], float(data['price'].strip('$'))))
        conn.commit()
        conn.close()
        
        confirmation_message = "✅ **تم نشر الخدمة بنجاح!**"
        
    elif query.data == 'dev_cancel_service_post':
        confirmation_message = "❌ **تم إلغاء عملية النشر.**"
    
    await query.edit_message_caption(
        caption=confirmation_message,
        reply_markup=None,
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END

# --- Handlers: Charge Post ---
async def start_new_charge_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("عذراً، هذا الأمر خاص بالمطور.")
        return ConversationHandler.END

    await update.message.reply_text("📝 **نشر عرض شحن جديد**\n\nالرجاء إرسال **اسم الزر/العرض** (مثلاً: 6480 كريستالة):", parse_mode='Markdown')
    context.user_data['charge_data'] = {}
    return GET_CHARGE_BUTTON_NAME

async def get_charge_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['charge_data']['button_name'] = update.message.text.strip()
        try:
             await update.message.delete()
        except Exception:
             pass
             
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🖼️ أحسنت. الآن، الرجاء إرسال **صورة واحدة** للعرض:",
            parse_mode='Markdown'
        )
        return GET_CHARGE_PHOTO
    else:
        await update.message.reply_text("❌ يرجى إرسال اسم الزر كنص. حاول مجدداً أو أرسل /cancel.")
        return GET_CHARGE_BUTTON_NAME

async def get_charge_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['charge_data']['photo_id'] = update.message.photo[-1].file_id
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="📝 أحسنت. الآن، الرجاء إرسال **تفاصيل العرض الكاملة** (نص وصفي):",
            parse_mode='Markdown'
        )
        return GET_CHARGE_DETAILS
    else:
        await update.message.reply_text("❌ الرجاء إرسال صورة واحدة فقط. حاول مجدداً أو أرسل /cancel.")
        return GET_CHARGE_PHOTO

async def get_charge_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text:
        context.user_data['charge_data']['details'] = update.message.text
        await update.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="💵 ممتاز. الآن، الرجاء إرسال **سعر العرض** (بالأرقام فقط):",
            parse_mode='Markdown'
        )
        return GET_CHARGE_PRICE
    else:
        await update.message.reply_text("❌ الرجاء إرسال التفاصيل كنص. حاول مجدداً أو أرسل /cancel.")
        return GET_CHARGE_DETAILS

async def get_charge_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        if price <= 0:
            raise ValueError
        context.user_data['charge_data']['price'] = f"{price:.2f}$"
        
        await update.message.delete()
        
        data = context.user_data['charge_data']
        button_name = data['button_name']
        
        preview_caption = (
            f"✨ **معاينة عرض الشحن (للمطور)** ✨\n\n"
            f"**الاسم:** {button_name}\n"
            f"**التفاصيل:**\n{data['details']}\n\n"
            f"**السعر:** {data['price']}"
        )
        
        preview_keyboard = [
            [
                InlineKeyboardButton("✅ نشر الآن", callback_data='dev_publish_charge_now'),
                InlineKeyboardButton("❌ إلغاء", callback_data='dev_cancel_charge_post')
            ]
        ]
        
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=data['photo_id'],
            caption=preview_caption,
            reply_markup=InlineKeyboardMarkup(preview_keyboard),
            parse_mode='Markdown'
        )
        return AWAIT_CHARGE_CONFIRMATION

    except ValueError:
        await update.message.reply_text("❌ السعر يجب أن يكون رقماً صحيحاً أو عشرياً موجباً. حاول مجدداً أو أرسل /cancel.")
        return GET_CHARGE_PRICE
        
async def post_charge_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = context.user_data['charge_data']
    
    if query.data == 'dev_publish_charge_now':
        
        charge_id = f"CHR{generate_transaction_id()}" 
        
        # حفظ عرض الشحن في قاعدة البيانات
        conn = db_connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO charges (id, button_name, photo_id, details, price) 
            VALUES (?, ?, ?, ?, ?)
        """, (charge_id, data['button_name'], data['photo_id'], data['details'], float(data['price'].strip('$'))))
        conn.commit()
        conn.close()
        
        confirmation_message = "✅ **تم نشر عرض الشحن بنجاح!**"
        
    elif query.data == 'dev_cancel_charge_post':
        confirmation_message = "❌ **تم إلغاء عملية النشر.**"
    
    await query.edit_message_caption(
        caption=confirmation_message,
        reply_markup=None,
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return ConversationHandler.END


# --- Handlers: Errors & Unknown Commands ---
# 📌 هذه الدالة هي التي كانت تسبب خطأ NameError
async def channel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("وظيفة القناة القديمة لم تعد نشطة.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يسجل الأخطاء التي تسببها التحديثات."""
    print(f'Update {update} caused error {context.error}')

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ترد على أي رسالة نصية/أمر لم يتم معالجته بواسطة handlers أخرى."""
    if update.message and update.message.text and update.message.text.startswith('/'):
        await update.message.reply_text("عذراً، هذا الأمر غير معروف.")

# =========================================================
#                         التشغيل
# =========================================================

def main():
    """نقطة البداية لتشغيل البوت."""
    
    db_initialize() 
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 📌 تشغيل النبضات الحية في خيط منفصل (الحل لمشكلة الاستضافة)
    start_heartbeat_thread(application) 

    
    # 1. معالج محادثة أمر "نشر حساب"
    post_now_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^(نشر حساب|اضف حساب)$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, start_new_post)
        ],
        states={
            GET_POST_PHOTO: [MessageHandler(filters.PHOTO, get_post_photo)],
            GET_POST_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_post_details)],
            GET_POST_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_post_price)],
            GET_POST_SELLER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_post_seller_id)], 
            AWAIT_POST_CONFIRMATION: [CallbackQueryHandler(post_action_handler, pattern='^dev_(publish_now|cancel_post)$')]
        },
        fallbacks=[CommandHandler("cancel", cancel_new_post_command)], 
        per_user=True,
    )
    application.add_handler(post_now_handler)
    
    # 2. معالج محادثة أمر "اضف خدمة"
    service_post_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^(اضف خدمة)$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, start_new_service_post)
        ],
        states={
            GET_SERVICE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service_name)], 
            GET_SERVICE_PHOTO: [MessageHandler(filters.PHOTO, get_service_photo)],
            GET_SERVICE_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service_details)],
            GET_SERVICE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_service_price)],
            AWAIT_SERVICE_CONFIRMATION: [CallbackQueryHandler(post_service_action_handler, pattern='^dev_(publish_service_now|cancel_service_post)$')]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)], 
        per_user=True,
    )
    application.add_handler(service_post_handler)
    
    # 3. معالج محادثة أمر "اضف شحن"
    charge_post_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^(اضف شحن)$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, start_new_charge_post)
        ],
        states={
            GET_CHARGE_BUTTON_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_charge_button_name)], 
            GET_CHARGE_PHOTO: [MessageHandler(filters.PHOTO, get_charge_photo)],
            GET_CHARGE_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_charge_details)],
            GET_CHARGE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_charge_price)],
            AWAIT_CHARGE_CONFIRMATION: [CallbackQueryHandler(post_charge_action_handler, pattern='^dev_(publish_charge_now|cancel_charge_post)$')]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)], 
        per_user=True,
    )
    application.add_handler(charge_post_handler)

    # 4. معالج محادثة أمر "حذف"
    delete_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^حذف$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, start_delete_process)
        ],
        states={
            AWAITING_DELETE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_delete_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)], 
        per_user=True,
    )
    application.add_handler(delete_handler)
    
    # 5. معالج محادثة أمر "كشف"
    reveal_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^كشف$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, start_reveal_process)
        ],
        states={
            AWAITING_REVEAL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_reveal_id)]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)], 
        per_user=True,
    )
    application.add_handler(reveal_handler)
    
    # 6. معالج أمر /start
    application.add_handler(CommandHandler("start", start))
    
    # 7. معالج الأزرار الشفافة الرئيسية
    application.add_handler(CallbackQueryHandler(referral_details, pattern='^show_referral$'))
    application.add_handler(CallbackQueryHandler(go_back_to_start, pattern='^go_back_to_start$'))
    
    # 8. معالج زر الخدمات والتنقل
    application.add_handler(CallbackQueryHandler(show_services_menu, pattern='^show_services_menu$'))
    application.add_handler(CallbackQueryHandler(show_service_offers, pattern='^show_service_offers$'))
    application.add_handler(CallbackQueryHandler(handle_service_navigation, pattern='^service_(next|prev)_'))
    application.add_handler(CallbackQueryHandler(handle_service_purchase, pattern='^buy_service_'))
    
    # 9. معالج زر شحن الكرستالات والتنقل
    application.add_handler(CallbackQueryHandler(show_charge_offers, pattern='^show_charge_offers$'))
    application.add_handler(CallbackQueryHandler(handle_charge_navigation, pattern='^charge_(next|prev)_'))
    application.add_handler(CallbackQueryHandler(handle_charge_purchase, pattern='^buy_charge_'))
    
    # 10. معالج زر الحسابات والشراء والفلترة
    application.add_handler(CallbackQueryHandler(show_accounts, pattern='^show_available_accounts$'))
    application.add_handler(CallbackQueryHandler(handle_purchase, pattern='^buy_'))
    application.add_handler(CallbackQueryHandler(handle_navigation, pattern='^nav_'))
    
    # 11. معالج أوامر المطور (إضافة/خصم مبلغ)
    dev_ops_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^اضف رصيد$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, add_balance_command),
            MessageHandler(filters.Regex(r'^خصم رصيد$') & filters.User(DEVELOPER_ID) & ~filters.COMMAND, deduct_balance_command)
        ],
        states={
            GETTING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_id)],
            GETTING_AMOUNT_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_amount_add)],
            GETTING_AMOUNT_DEDUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_amount_deduct)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)], 
        per_user=True 
    )
    application.add_handler(dev_ops_handler)

    
    # 12. معالج محادثة الفلترة 
    filter_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(filter_start, pattern='^filter_start$')],
        states={
            GET_FILTER_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_filter_price)]
        },
        fallbacks=[CommandHandler("cancel", cancel_filter), CallbackQueryHandler(cancel_filter, pattern='^cancel_filter$')],
        per_user=True
    )
    application.add_handler(filter_handler)
    
    # 13. معالج أزرار القناة (تم وضعه كـ CallBack عادي)
    application.add_handler(CallbackQueryHandler(channel_action, pattern=r'^(publish|reject)_\d+$'))
    
    # 14. معالج الأخطاء وتسجيل الأخطاء
    application.add_error_handler(error_handler)
    
    # 15. معالج الأوامر غير المعروفة
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    
    print("البوت بدأ العمل...")
    application.run_polling()

if __name__ == '__main__':
    main()