import os
import sys
import time
import threading
import requests
import urllib.parse
from flask import Flask
import telebot
from telebot import types

# محاولة تحميل ملف .env إن وجد
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==========================================
# 1. إعدادات التوكن ومفاتيح الذكاء الاصطناعي
# ==========================================
# يمكنك وضع توكن البوت هنا مباشرة أو في ملف .env تحت اسم TELEGRAM_BOT_TOKEN
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# مفاتيح اختيارية لتحسين الذكاء الاصطناعي (Gemini / OpenAI)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("[WARNING] لم يتم تعيين TELEGRAM_BOT_TOKEN بعد! يرجى وضع توكن البوت من @BotFather في ملف .env أو في الكود.")
    # لا نوقف البرنامج حتى يعمل خادم الويب على الأقل في السحاب

bot = telebot.TeleBot(TOKEN) if TOKEN and TOKEN != "YOUR_BOT_TOKEN_HERE" else None

# ذاكرة لتخزين أوضاع المستخدم الحالي (بحث كتب / محادثة ذكاء اصطناعي / ترجمة / كتابة مقال)
user_states = {}
# ذاكرة لتخزين تفاصيل الكتب المحددة لتلخيصها
books_cache = {}

# ==========================================
# 2. محركات الذكاء الاصطناعي المساعدة (AI Engines)
# ==========================================
def get_ai_response(prompt, system_instruction="أنت مساعد ذكي ومثقف وودود باللغة العربية، تجيب بوضوح ودقة وترتيب مع استخدام الأيموجي والعناوين."):
    """
    يجلب الرد الذكي سواء عبر Gemini أو Pollinations AI المجانية السريعة.
    """
    # 1. محاولة استخدام Gemini إن كان المفتاح متوفراً
    if GEMINI_API_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": f"{system_instruction}\n\nسؤال المستخدم: {prompt}"}]}]
            }
            r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if r.status_code == 200:
                text = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text")
                if text:
                    return text.strip()
        except Exception as e:
            print(f"[AI Fallback] Gemini failed: {e}")

    # 2. استخدام محرك Pollinations AI المجاني والممتاز (لا يحتاج مفاتيح)
    try:
        full_prompt = f"{system_instruction}\n\nالمطلوب: {prompt}"
        safe_prompt = urllib.parse.quote(full_prompt)
        ai_url = f"https://text.pollinations.ai/{safe_prompt}?model=openai"
        r = requests.get(ai_url, timeout=25)
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
    except Exception as e:
        print(f"[AI Fallback] Pollinations failed: {e}")

    # 3. رد احتياطي في حال تعذر الاتصال بالمحركات
    return "💡 عذراً، أواجه ضغطاً مؤقتاً في شبكة الذكاء الاصطناعي. يرجى المحاولة بعد لحظات أو إعادة صياغة السؤال!"

# ==========================================
# 3. محرك بحث الكتب والمكتبات العالمية (Google Books + Open Library)
# ==========================================
def search_books_engine(query):
    results = []
    try:
        # البحث في Google Books API
        url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=5&printType=books"
        r = requests.get(url, timeout=12)
        if r.status_code == 200:
            data = r.json()
            for item in data.get('items', []):
                vol = item.get('volumeInfo', {})
                title = vol.get('title', 'بدون عنوان')
                authors = ", ".join(vol.get('authors', [])) or "مؤلف غير معروف"
                year = (vol.get('publishedDate') or 'غير محدد')[:4]
                pages = vol.get('pageCount', 'غير محدد')
                desc = vol.get('description', '')[:350]
                if desc and len(vol.get('description', '')) > 350:
                    desc += "..."
                rating = vol.get('averageRating', '--')
                images = vol.get('imageLinks', {})
                cover = images.get('thumbnail') or images.get('smallThumbnail')
                if cover and cover.startswith("http://"):
                    cover = cover.replace("http://", "https://")
                preview_link = vol.get('previewLink') or vol.get('infoLink')

                book_id = item.get('id')
                book_data = {
                    'id': book_id,
                    'title': title,
                    'authors': authors,
                    'year': year,
                    'pages': pages,
                    'desc': desc or 'لا يوجد ملخص متاح حالياً لهذا الكتاب.',
                    'rating': rating,
                    'cover': cover,
                    'preview': preview_link
                }
                books_cache[book_id] = book_data
                results.append(book_data)
    except Exception as e:
        print(f"[Books Search Error]: {e}")
    return results

# ==========================================
# 4. أوامر البوت وقائمة التحكم الرئيسية
# ==========================================
def get_main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📚 بحث في المكتبة والكتب", callback_data="mode_books"),
        types.InlineKeyboardButton("🤖 التحدث مع الذكاء الاصطناعي", callback_data="mode_chat"),
        types.InlineKeyboardButton("✍️ تلخيص كتب ومقالات", callback_data="mode_summary"),
        types.InlineKeyboardButton("🌐 مترجم اللغات الذكي", callback_data="mode_translate")
    )
    markup.add(types.InlineKeyboardButton("ℹ️ مساعدة وطريقة الاستخدام", callback_data="mode_help"))
    return markup

if bot:
    @bot.message_handler(commands=['start', 'help', 'menu'])
    def handle_start(message):
        chat_id = message.chat.id
        user_states[chat_id] = "menu"
        welcome_text = (
            f"🌟 <b>أهلاً بك في «بوت المكتبة والذكاء الاصطناعي» الشامل! 🤖📚</b>\n\n"
            f"أنا مساعدك الشخصي الذكي، أجمع بين <b>موسوعة الكتب والمراجع العالمية</b> وبين <b>قدرات الذكاء الاصطناعي المتقدمة</b> لتسهيل دراستك، أبحاثك، وقراءتك اليومية.\n\n"
            f"👇 <b>اختر أحد الأقسام من القائمة التفاعلية أدناه للبدء:</b>"
        )
        bot.send_message(chat_id, welcome_text, reply_markup=get_main_menu_markup())

    @bot.callback_query_handler(func=lambda call: call.data.startswith("mode_") or call.data.startswith("show_book_") or call.data.startswith("ai_book_"))
    def handle_callbacks(call):
        chat_id = call.message.chat.id
        data = call.data

        if data == "mode_books":
            user_states[chat_id] = "waiting_book_query"
            msg = (
                "📚 <b>وضع البحث في المكتبة العالمية:</b>\n\n"
                "✏️ أرسل لي الآن <b>اسم الكتاب، أو اسم المؤلف، أو موضوع الكتاب</b> الذي تبحث عنه، وسأجلب لك التفاصيل وملخصه وروابط القراءة والتحميل فوراً!"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_chat":
            user_states[chat_id] = "waiting_ai_chat"
            msg = (
                "🤖 <b>وضع التحدث والمساعدة الذكية:</b>\n\n"
                "💬 اسألني عن أي شيء يدور في ذهنك! سواء في العلوم، البرمجة، الفلسفة، أو استشارة عامة، وسأجيبك بذكاء وتفصيل."
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_summary":
            user_states[chat_id] = "waiting_ai_summary"
            msg = (
                "✍️ <b>وضع تلخيص الكتب والمقالات:</b>\n\n"
                "📋 أرسل لي الآن <b>نصاً طويلاً، أو اسم كتاب تريد تلخيص فصوله وأفكاره الرئيسية</b> وسأقوم بإعداد ملخص شامل ومنظم لك بثوانٍ!"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_translate":
            user_states[chat_id] = "waiting_ai_translate"
            msg = (
                "🌐 <b>وضع الترجمة الذكية الاحترافية:</b>\n\n"
                "🔄 أرسل لي أي نص بلغة أجنبية وسأترجمه لك إلى العربية الفصحى بدقة متناهية (أو العكس إذا أرسلت نصاً عربياً)."
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_help":
            msg = (
                "ℹ️ <b>دليل استخدام بوت المكتبة والذكاء الاصطناعي:</b>\n\n"
                "• <b>لبحث الكتب:</b> اضغط على زر (📚 بحث في المكتبة) واكتب اسم الكتاب للوصول إلى الغلاف والتقييم وروابط التحميل الـ PDF المجانية.\n"
                "• <b>للتلخيص الذكي:</b> اضغط على (✍️ تلخيص) وأرسل اسم أي كتاب مشهور، وسيقوم الذكاء الاصطناعي باستخراج أهم 5 أفكار رئيسية منه.\n"
                "• <b>للأسئلة العامة:</b> اضغط على (🤖 التحدث مع الذكاء الاصطناعي) واسألني في أي مجال.\n\n"
                "👇 يمكنك العودة للقائمة الرئيسية في أي وقت بالضغط على الأزرار أدناه:"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data.startswith("show_book_"):
            book_id = data.split("show_book_")[1]
            book = books_cache.get(book_id)
            if not book:
                bot.answer_callback_query(call.id, "❌ عذراً، انتهت صلاحية هذا الرابط. يرجى البحث مجدداً.")
                return
            
            bot.answer_callback_query(call.id, "📖 جاري جلب بطاقة الكتاب والبوستر...")
            send_book_details_card(chat_id, book)

        elif data.startswith("ai_book_"):
            book_id = data.split("ai_book_")[1]
            book = books_cache.get(book_id)
            if not book:
                bot.answer_callback_query(call.id, "❌ يرجى البحث عن الكتاب أولاً.")
                return

            bot.answer_callback_query(call.id, "🧠 جاري التحليل والتلخيص بالذكاء الاصطناعي...")
            status_msg = bot.send_message(chat_id, f"🧠 <b>جاري إعداد تلخيص وتحليل شامل لأفكار كتاب:</b>\n«{book['title']}» ⏳")
            
            threading.Thread(target=process_book_ai_summary, args=(chat_id, book, status_msg)).start()

    def send_book_details_card(chat_id, book):
        title = book['title']
        authors = book['authors']
        year = book['year']
        pages = book['pages']
        desc = book['desc']
        rating = book['rating']
        cover = book['cover']
        preview = book['preview']

        safe_title = urllib.parse.quote(f"{title} {authors}")
        pdf_search_url = f"https://www.google.com/search?q=تحميل+كتاب+{safe_title}+pdf+مجانا"

        caption = (
            f"📕 <b>{title}</b> ({year})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✍️ <b>المؤلف:</b> {authors}\n"
            f"📄 <b>الصفحات:</b> {pages} صفحة | ⭐ <b>التقييم:</b> {rating}/5\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📖 <b>نبذة وسطور عن الكتاب:</b>\n"
            f"<i>{desc}</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>اختر أحد خيارات التحميل أو التلخيص الذكي من الأزرار أدناه:</i> 👇"
        )

        markup = types.InlineKeyboardMarkup(row_width=1)
        if preview:
            markup.add(types.InlineKeyboardButton("📖 قراءة ومعاينة الكتاب (Google Books)", url=preview))
        markup.add(
            types.InlineKeyboardButton("⚡ بحث مباشر عن روابط تحميل PDF المجانية", url=pdf_search_url),
            types.InlineKeyboardButton("🧠 تلخيص وشرح أفكار الكتاب بالذكاء الاصطناعي", callback_data=f"ai_book_{book['id']}"),
            types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
        )

        if cover:
            bot.send_photo(chat_id, cover, caption=caption, reply_markup=markup)
        else:
            bot.send_message(chat_id, caption, reply_markup=markup)

    def process_book_ai_summary(chat_id, book, status_msg):
        prompt = (
            f"قم بإعداد ملخص منظم وشامل باللغة العربية لكتاب «{book['title']}» من تأليف «{book['authors']}».\n"
            f"اذكر أهم 5 أفكار رئيسية يناقشها الكتاب، والفئة المستهدفة، ولماذا يستحق القراءة، بأسلوب شيق وجذاب."
        )
        try:
            summary_text = get_ai_response(prompt, system_instruction="أنت ناقد أدبي ومستشار قراءة محترف تجيب بلغة عربية فصحى راقية.")
            bot.edit_message_text(
                f"📕 <b>تلخيص وتحليل الذكاء الاصطناعي لكتاب:</b> «{book['title']}»\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"{summary_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>هل لديك أي سؤال محدد حول هذا الكتاب؟ يمكنك طرحه الآن في وضع التحدث!</i> 🤖",
                chat_id=chat_id,
                message_id=status_msg.message_id,
                reply_markup=get_main_menu_markup()
            )
        except Exception as e:
            bot.edit_message_text(f"❌ عذراً، تعذر إتمام التلخيص الذكي حالياً: {e}", chat_id=chat_id, message_id=status_msg.message_id)

    @bot.message_handler(func=lambda message: True)
    def handle_text_messages(message):
        chat_id = message.chat.id
        text = message.text.strip()
        state = user_states.get(chat_id, "menu")

        if state == "waiting_book_query":
            status_msg = bot.send_message(chat_id, f"⏳ <b>جاري البحث في المكتبة العالمية عن:</b> «{text}» 🔎")
            threading.Thread(target=process_book_search_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_chat":
            status_msg = bot.send_message(chat_id, "🧠 <b>الذكاء الاصطناعي يفكر ويكتب الرد الآن...</b> ⚡")
            threading.Thread(target=process_ai_chat_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_summary":
            status_msg = bot.send_message(chat_id, "✍️ <b>جاري تحليل واستخراج الأفكار والتلخيص...</b> 📋")
            threading.Thread(target=process_ai_summary_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_translate":
            status_msg = bot.send_message(chat_id, "🌐 <b>جاري الترجمة الذكية الاحترافية...</b> 🔄")
            threading.Thread(target=process_ai_translate_task, args=(chat_id, text, status_msg)).start()

        else:
            # إذا كتب نصوصاً دون اختيار وضع، نعيده للقائمة بأسلوب لطيف
            bot.send_message(
                chat_id,
                "👋 <b>مرحباً بك!</b> يرجى أولاً اختيار القسم الذي تريده من الأزرار أدناه (بحث كتب، أو محادثة ذكاء اصطناعي):",
                reply_markup=get_main_menu_markup()
            )

    def process_book_search_task(chat_id, query, status_msg):
        results = search_books_engine(query)
        if not results:
            bot.edit_message_text(
                f"❌ <b>لم يتم العثور على كتب مطابقة لـ:</b> «{query}»\n"
                f"💡 جرب التأكد من الإملاء أو كتابة اسم الكتاب باللغة الأصلية (الإنجليزية أو العربية).",
                chat_id=chat_id,
                message_id=status_msg.message_id,
                reply_markup=get_main_menu_markup()
            )
            return

        markup = types.InlineKeyboardMarkup(row_width=1)
        for idx, book in enumerate(results[:5], 1):
            btn_text = f"📕 {idx}. {book['title'][:35]} | ({book['year']}) ⭐ {book['rating']}"
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"show_book_{book['id']}"))

        bot.edit_message_text(
            f"📚 <b>نتائج بحث المكتبة عن:</b> <i>«{query}»</i>\n\n"
            f"👇 <i>اختر الكتاب الذي تريده لعرض بطاقته الكاملة، الغلاف، روابط التحميل المجانية، والتلخيص الذكي:</i>",
            chat_id=chat_id,
            message_id=status_msg.message_id,
            reply_markup=markup
        )

    def process_ai_chat_task(chat_id, prompt, status_msg):
        response = get_ai_response(prompt)
        bot.edit_message_text(f"🤖 <b>رد المساعد الذكي:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

    def process_ai_summary_task(chat_id, text, status_msg):
        prompt = f"قم بتلخيص وتفكيك هذا النص أو الكتاب إلى نقاط واضحة وموجزة ومرتبة:\n\n{text}"
        response = get_ai_response(prompt, system_instruction="أنت خبير تلخيص أكاديمي ومثقف تجيب بأسلوب منظم جداً ونقاط واضحة.")
        bot.edit_message_text(f"📋 <b>التلخيص الذكي:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

    def process_ai_translate_task(chat_id, text, status_msg):
        prompt = f"ترجم هذا النص ترجمة أدبية واحترافية دقيقة إلى اللغة العربية الفصحى (وإذا كان بالعربية ترجمه إلى الإنجليزية بأسلوب متقن):\n\n{text}"
        response = get_ai_response(prompt, system_instruction="أنت مترجم فوري محترف تتقن اللغتين العربية والإنجليزية ببراعة فائقة.")
        bot.edit_message_text(f"🌐 <b>الترجمة الاحترافية:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

# ==========================================
# 5. خادم الويب للحفاظ على البوت نشطاً 24/7 على Render
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    status = "Online 🟢" if bot else "Missing Token ⚠️"
    return f"<h1>📚🤖 AI & Library Assistant Bot is Alive 24/7! (Status: {status})</h1><p>Ready to search global book libraries and provide intelligent AI answers instantly.</p>"

def run_bot_polling():
    if not bot:
        print("[ERROR] لم يتم تشغيل البوت بسبب عدم وجود TELEGRAM_BOT_TOKEN.")
        return
    while True:
        try:
            print("[INFO] AI & Library Bot is polling Telegram...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"[ERROR] AI & Library Bot restart due to: {e}")
            time.sleep(5)

polling_thread = threading.Thread(target=run_bot_polling, daemon=True)
polling_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
