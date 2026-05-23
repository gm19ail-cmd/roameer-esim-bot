import telebot
from telebot import types
import re
import io
import qrcode
from pyzbar.pyzbar import decode
from PIL import Image
import os
import csv
import threading
import http.server
import socketserver

# ========================================================
# الإعدادات الأساسية
# ========================================================
TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
bot = telebot.TeleBot(TOKEN)

user_data = {}

# تحميل خطط eSIM من ملف CSV
PLANS = []
CSV_PATH = os.path.join(os.path.dirname(__file__), 'esim-plans-page-1.csv')
try:
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            PLANS.append(row)
except Exception:
    pass


# ========================================================
# دوال مساعدة
# ========================================================

def init_user(chat_id):
    """تهيئة بيانات المستخدم إن لم تكن موجودة — يحمي من KeyError"""
    if chat_id not in user_data:
        user_data[chat_id] = {
            'mode': None,
            'items': [],
            'items_per_msg': 1,
            'logo': None,
            'awaiting': None,
        }


def extract_lpa_from_text(text):
    """استخراج أكواد LPA من نص الموردين"""
    pattern = r'(LPA:[\w\$\.\-]+)'
    return re.findall(pattern, text, re.IGNORECASE)


def build_activation_link(lpa_code):
    """
    بناء رابط تفعيل صحيح من كود LPA.
    يضمن عدم تكرار LPA: في الرابط.
    """
    clean = lpa_code.upper()
    if not clean.startswith('LPA:'):
        clean = 'LPA:' + clean
    return f"https://lpa.ee/{clean}"


def generate_qr_image(data):
    """توليد صورة QR من نص معطى"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


# ========================================================
# لوحات المفاتيح
# ========================================================

def get_main_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📝 نصوص متعددة", callback_data="mode_text"),
        types.InlineKeyboardButton("🖼️ صور متعددة (QR)", callback_data="mode_qr"),
    )
    markup.add(types.InlineKeyboardButton("📋 خطط eSIM المتاحة", callback_data="show_plans"))
    markup.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="mode_cancel"))
    return markup


def get_cancel_markup():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="mode_cancel"))
    return markup


def get_action_markup(items_per_msg):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔗 روابط فقط", callback_data="action_links"),
        types.InlineKeyboardButton("🔧 تفعيل يدوي (أكواد LPA)", callback_data="action_manual"),
        types.InlineKeyboardButton(f"⚙️ عدد الشرائح في الرسالة ({items_per_msg})", callback_data="action_count"),
        types.InlineKeyboardButton("💎 (VIP) فاخر QR", callback_data="action_vip"),
        types.InlineKeyboardButton("💾 حفظ/تغيير الشعار", callback_data="action_change"),
        types.InlineKeyboardButton("🗑️ حذف الشعار", callback_data="action_delete"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="action_cancel"),
    )
    return markup


# ========================================================
# أوامر البوت
# ========================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    init_user(chat_id)
    user_data[chat_id]['mode'] = None
    user_data[chat_id]['items'] = []
    user_data[chat_id]['awaiting'] = None

    bot.send_message(
        chat_id,
        "📲 *نظام تفعيل eSIM لمتجر Roameer*\n\nاختر الطريقة لبدء العمل:",
        parse_mode="Markdown",
        reply_markup=get_main_markup()
    )


# ========================================================
# عرض خطط eSIM
# ========================================================

@bot.callback_query_handler(func=lambda call: call.data == "show_plans")
def show_plans(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if not PLANS:
        bot.send_message(chat_id, "❌ لا توجد خطط متاحة حالياً.")
        return

    chunks = []
    current = "📋 *خطط eSIM المتاحة:*\n\n"

    for i, plan in enumerate(PLANS, 1):
        activation = plan['Activation']
        if len(activation) > 80:
            activation = activation[:80] + "..."

        entry = (
            f"*{i}. {plan['Country']}*\n"
            f"   💾 البيانات: {plan['Data']}\n"
            f"   ⏱️ المدة: {plan['Duration']}\n"
            f"   💰 السعر: ${plan['Price']}\n"
            f"   📶 الشبكة: {plan['Network']}\n"
            f"   🔑 الكود: `{plan['Product Code']}`\n"
            f"   ⚡ التفعيل: {activation}\n\n"
        )

        if len(current) + len(entry) > 3800:
            chunks.append(current)
            current = entry
        else:
            current += entry

    if current:
        chunks.append(current)

    for chunk in chunks:
        bot.send_message(chat_id, chunk, parse_mode="Markdown")


# ========================================================
# اختيار الوضع
# ========================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def set_mode(call):
    chat_id = call.message.chat.id
    mode = call.data.split("_")[1]
    init_user(chat_id)
    bot.answer_callback_query(call.id)

    if mode == "cancel":
        user_data[chat_id]['mode'] = None
        user_data[chat_id]['items'] = []
        user_data[chat_id]['awaiting'] = None
        bot.send_message(chat_id, "🔙 تم الإلغاء. اختر طريقة جديدة:", reply_markup=get_main_markup())
        return

    user_data[chat_id]['mode'] = mode
    user_data[chat_id]['items'] = []
    user_data[chat_id]['awaiting'] = None

    if mode == "qr":
        bot.send_message(
            chat_id,
            "🖼️ أرسل صور QR الآن، وبعد الانتهاء اكتب:\n*تم*",
            parse_mode="Markdown",
            reply_markup=get_cancel_markup()
        )
    elif mode == "text":
        bot.send_message(
            chat_id,
            "📝 أرسل نصوص أو أكواد الموردين الآن، وبعد الانتهاء اكتب:\n*تم*",
            parse_mode="Markdown",
            reply_markup=get_cancel_markup()
        )


# ========================================================
# معالجة الصور (QR + شعار)
# ========================================================

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    init_user(chat_id)

    # وضع رفع الشعار
    if user_data[chat_id].get('awaiting') == 'logo_input':
        user_data[chat_id]['logo'] = message.photo[-1].file_id
        user_data[chat_id]['awaiting'] = None
        bot.reply_to(message, "✅ تم حفظ الشعار بنجاح!", reply_markup=get_main_markup())
        return

    # وضع قراءة QR
    if user_data[chat_id].get('mode') != 'qr':
        bot.reply_to(message, "⚠️ اختر (🖼️ صور متعددة) من القائمة أولاً.", reply_markup=get_main_markup())
        return

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image = Image.open(io.BytesIO(downloaded_file))
        image.thumbnail((800, 800))
        decoded_objects = decode(image)

        if not decoded_objects:
            bot.reply_to(message, "❌ لم يتم التعرف على QR. تأكد من وضوح الصورة.")
            return

        lpa_text = decoded_objects[0].data.decode('utf-8')
        lpa_codes = extract_lpa_from_text(lpa_text)

        if not lpa_codes:
            bot.reply_to(message, "❌ تم قراءة QR لكن لا يوجد كود LPA مطابق.")
            return

        code = lpa_codes[0]
        if code not in user_data[chat_id]['items']:
            user_data[chat_id]['items'].append(code)
            bot.reply_to(
                message,
                f"✅ تمت الإضافة.\n📦 المجموع: *{len(user_data[chat_id]['items'])}*",
                parse_mode="Markdown"
            )
        else:
            bot.reply_to(message, "⚠️ هذه الشريحة مضافة مسبقاً.")

    except Exception as e:
        bot.reply_to(message, f"⚠️ خطأ في معالجة الصورة:\n`{str(e)}`", parse_mode="Markdown")


# ========================================================
# معالجة النصوص وأمر "تم"
# ========================================================

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text.strip()
    init_user(chat_id)

    # وضع إدخال عدد الشرائح
    if user_data[chat_id].get('awaiting') == 'count_input':
        try:
            n = int(text)
            if 1 <= n <= 20:
                user_data[chat_id]['items_per_msg'] = n
                user_data[chat_id]['awaiting'] = None
                bot.reply_to(
                    message,
                    f"✅ تم التعيين: *{n}* شريحة لكل رسالة.",
                    parse_mode="Markdown",
                    reply_markup=get_main_markup()
                )
            else:
                bot.reply_to(message, "⚠️ أدخل رقماً بين 1 و 20.")
        except ValueError:
            bot.reply_to(message, "⚠️ الرجاء إدخال رقم صحيح فقط.")
        return

    # لو لم يختر وضعاً بعد
    if not user_data[chat_id]['mode']:
        bot.send_message(chat_id, "👇 اختر طريقة العمل أولاً:", reply_markup=get_main_markup())
        return

    # أمر الإنهاء
    if text in ['تم', 'done', 'جاهز']:
        items = user_data[chat_id]['items']
        if not items:
            bot.send_message(chat_id, "❌ لم تتم إضافة أي شرائح بعد.")
            return

        n = user_data[chat_id].get('items_per_msg', 1)
        bot.send_message(
            chat_id,
            f"✅ *{len(items)} شريحة جاهزة*\n⚙️ الإعداد الحالي: {n} شريحة لكل رسالة\n\nاختر طريقة الإرسال:",
            parse_mode="Markdown",
            reply_markup=get_action_markup(n)
        )
        return

    # وضع النصوص
    if user_data[chat_id]['mode'] == 'text':
        lpa_codes = extract_lpa_from_text(text)
        if not lpa_codes:
            bot.reply_to(message, "❌ النص لا يحتوي على كود LPA قياسي.")
            return

        added = 0
        for code in lpa_codes:
            if code not in user_data[chat_id]['items']:
                user_data[chat_id]['items'].append(code)
                added += 1

        duplicates = len(lpa_codes) - added
        reply = f"✅ تمت إضافة *{added}* شريحة.\n📦 المجموع: *{len(user_data[chat_id]['items'])}*"
        if duplicates:
            reply += f"\n⚠️ تجاهل *{duplicates}* مكررة."
        bot.reply_to(message, reply, parse_mode="Markdown")


# ========================================================
# دوال الإرسال
# ========================================================

def send_links(chat_id, items, items_per_msg, logo_file_id=None):
    """إرسال روابط التفعيل مجمعة حسب الإعداد"""
    total = len(items)
    bot.send_message(chat_id, f"📦 جاري إرسال *{total}* شريحة...", parse_mode="Markdown")

    if logo_file_id:
        bot.send_photo(chat_id, logo_file_id, caption="🏪 Roameer eSIM")

    for i in range(0, total, items_per_msg):
        chunk = items[i:i + items_per_msg]
        text = ""
        for j, lpa_code in enumerate(chunk, i + 1):
            link = build_activation_link(lpa_code)
            text += f"📱 *شريحة {j}*\n━━━━━━━━━━━━━━━━━━\n{link}\n\n"
        bot.send_message(chat_id, text.strip(), parse_mode="Markdown", disable_web_page_preview=False)


def send_manual_codes(chat_id, items, items_per_msg):
    """إرسال أكواد LPA الخام للتفعيل اليدوي"""
    total = len(items)
    bot.send_message(chat_id, f"🔧 إرسال *{total}* كود LPA للتفعيل اليدوي...", parse_mode="Markdown")

    for i in range(0, total, items_per_msg):
        chunk = items[i:i + items_per_msg]
        text = ""
        for j, lpa_code in enumerate(chunk, i + 1):
            text += f"📱 *شريحة {j}*\n`{lpa_code}`\n\n"
        bot.send_message(chat_id, text.strip(), parse_mode="Markdown")


def send_vip_qr(chat_id, items):
    """توليد وإرسال كروت QR فاخرة لكل شريحة"""
    total = len(items)
    bot.send_message(chat_id, f"💎 جاري توليد *{total}* كرت QR فاخر...", parse_mode="Markdown")

    for idx, lpa_code in enumerate(items, 1):
        link = build_activation_link(lpa_code)
        qr_buf = generate_qr_image(link)
        caption = f"💎 *شريحة {idx}*\n{link}"
        bot.send_photo(chat_id, qr_buf, caption=caption, parse_mode="Markdown")


# ========================================================
# معالجة أزرار الأكشن
# ========================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("action_"))
def handle_actions(call):
    chat_id = call.message.chat.id
    action = call.data.split("_")[1]
    init_user(chat_id)
    bot.answer_callback_query(call.id)

    items = user_data[chat_id].get('items', [])
    items_per_msg = user_data[chat_id].get('items_per_msg', 1)
    logo = user_data[chat_id].get('logo')

    if action == "cancel":
        user_data[chat_id]['mode'] = None
        user_data[chat_id]['items'] = []
        user_data[chat_id]['awaiting'] = None
        bot.send_message(chat_id, "❌ تم الإلغاء.", reply_markup=get_main_markup())
        return

    if action in ['links', 'manual', 'vip'] and not items:
        bot.send_message(chat_id, "❌ القائمة فارغة أو انتهت الجلسة.")
        return

    if action == "links":
        send_links(chat_id, items, items_per_msg, logo)
        user_data[chat_id]['mode'] = None
        user_data[chat_id]['items'] = []
        bot.send_message(chat_id, "✅ تم الإرسال! هل تريد البدء من جديد؟", reply_markup=get_main_markup())

    elif action == "manual":
        send_manual_codes(chat_id, items, items_per_msg)
        user_data[chat_id]['mode'] = None
        user_data[chat_id]['items'] = []
        bot.send_message(chat_id, "✅ تم الإرسال! هل تريد البدء من جديد؟", reply_markup=get_main_markup())

    elif action == "count":
        user_data[chat_id]['awaiting'] = 'count_input'
        bot.send_message(
            chat_id,
            f"⚙️ الإعداد الحالي: *{items_per_msg}* شريحة لكل رسالة.\n\nأرسل الرقم الجديد (1-20):",
            parse_mode="Markdown"
        )

    elif action == "vip":
        send_vip_qr(chat_id, items)
        user_data[chat_id]['mode'] = None
        user_data[chat_id]['items'] = []
        bot.send_message(chat_id, "✅ تم إرسال الكروت! هل تريد البدء من جديد؟", reply_markup=get_main_markup())

    elif action == "change":
        user_data[chat_id]['awaiting'] = 'logo_input'
        current = "✅ يوجد شعار محفوظ." if logo else "❌ لا يوجد شعار حالياً."
        bot.send_message(chat_id, f"💾 {current}\n\nأرسل صورة الشعار الجديد:")

    elif action == "delete":
        if logo:
            user_data[chat_id]['logo'] = None
            bot.send_message(chat_id, "🗑️ تم حذف الشعار بنجاح.", reply_markup=get_main_markup())
        else:
            bot.send_message(chat_id, "⚠️ لا يوجد شعار محفوظ أصلاً.", reply_markup=get_main_markup())


# ========================================================
# سيرفر الويب لـ Hugging Face + UptimeRobot
# ========================================================

def run_dummy_server():
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"Roameer eSIM Bot is Running!")

        def log_message(self, format, *args):
            pass  # إيقاف logs السيرفر

    with socketserver.TCPServer(("", 7860), CustomHandler) as httpd:
        httpd.serve_forever()


# ========================================================
# نقطة الانطلاق
# ========================================================

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("🤖 Roameer eSIM Bot is running...")
    bot.infinity_polling(skip_pending=True)
