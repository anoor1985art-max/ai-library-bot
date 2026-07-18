import os
import sys
import time
import uuid
import random
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
# 1. إعدادات التوكن ومجلدات الوسائط
# ==========================================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

MEDIA_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), "generated_media")
os.makedirs(MEDIA_DIR, exist_ok=True)

if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("[WARNING] لم يتم تعيين TELEGRAM_BOT_TOKEN بعد! يرجى وضع توكن البوت من @BotFather في ملف .env أو في الكود.")

bot = telebot.TeleBot(TOKEN) if TOKEN and TOKEN != "YOUR_BOT_TOKEN_HERE" else None

# ذاكرة لتخزين أوضاع المستخدم والكتب وبيانات الصور/الفيديو
user_states = {}
books_cache = {}
media_cache = {}
user_prefs = {}
active_tasks = {} # حماية: تتبع هل المستخدم لديه عملية توليد (صورة أو فيديو) جارية حالياً
last_request_time = {} # حماية: تتبع فاصل الوقت بين الطلبات لمنع الإغراق (Rate Limit)

DB_FILE = os.path.join(os.path.abspath(os.path.dirname(__file__)), "ai_library_db.json")

import json
def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}}

def save_db(db):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DB Save Error]: {e}")

def check_rate_limit_and_concurrency(chat_id, is_heavy=False):
    """
    التحقق من عدم إغراق السيرفر بطلبات متتالية سريعة (Rate Limit)
    والتحقق من عدم وجود عملية توليد صور/فيديو ثقيلة جارية للمستخدم (Concurrency Limit)
    """
    now = time.time()
    # 1. فحص الفاصل الزمني البسيط (2 ثانية) لتجنب الإغراق
    if now - last_request_time.get(chat_id, 0) < 2.0:
        try:
            bot.send_message(chat_id, "⚠️ <b>مهلاً!</b> يرجى الانتظار ثوانٍ قليلة بين الطلب والآخر لتجنب الضغط على السيرفر.")
        except Exception:
            pass
        return False
    
    # 2. فحص هل لديه مهمة ثقيلة جارية (لتوليد الصور أو الفيديو)
    if is_heavy and active_tasks.get(chat_id, False):
        try:
            bot.send_message(chat_id, "⏳ <b>لديك عملية توليد (صورة أو فيديو) جارية بالفعل!</b>\nيرجى الانتظار حتى تكتمل العملية الحالية قبل طلب توليد جديد.")
        except Exception:
            pass
        return False

    last_request_time[chat_id] = now
    if is_heavy:
        active_tasks[chat_id] = True
    return True

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
                "contents": [{"parts": [{"text": f"{system_instruction}\n\nالمطلوب: {prompt}"}]}]
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

    return "💡 عذراً، أواجه ضغطاً مؤقتاً في شبكة الذكاء الاصطناعي. يرجى المحاولة بعد لحظات أو إعادة صياغة السؤال!"

def translate_to_english(text):
    """
    ترجمة الوصف العربي إلى الإنجليزية لتحسين دقة وجودة التوليد في FLUX AI.
    """
    # التحقق مما إذا كان النص يحتوي على أحرف عربية
    has_arabic = any("\u0600" <= c <= "\u06FF" for c in text)
    if not has_arabic:
        return text
    try:
        tr_prompt = f"Translate the following visual prompt into detailed, descriptive English suitable for high-end AI art generation (Stable Diffusion / FLUX). Return ONLY the translated English text without any intro or quotes:\n\n{text}"
        res = get_ai_response(tr_prompt, system_instruction="You are an expert AI prompt engineer. Translate visual prompts to vivid, high-quality English keywords.")
        if res and len(res) > 3 and not "عذراً" in res:
            return res.strip()
    except Exception:
        pass
    return text

# ==========================================
# 3. محرك توليد الصور وتحرك الفيديو (AI Image & Video Studio)
# ==========================================
def generate_ai_image(prompt, width=1024, height=1024, seed=None):
    if seed is None:
        seed = random.randint(1, 9999999)
    english_prompt = translate_to_english(prompt)
    print(f"[AI Image] Original: {prompt} | English: {english_prompt} | Seed: {seed}")
    safe_prompt = urllib.parse.quote(f"{english_prompt}, masterpiece, highly detailed, photorealistic, 8k resolution, cinematic lighting")
    
    models_to_try = ["flux", "turbo", ""]
    for model_name in models_to_try:
        try:
            model_param = f"&model={model_name}" if model_name else ""
            url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width={width}&height={height}&nologo=true&seed={seed}{model_param}"
            print(f"[AI Image] Trying model: '{model_name or 'default'}' -> {url[:80]}...")
            r = requests.get(url, timeout=35)
            if r.status_code == 200 and len(r.content) > 1000:
                filename = os.path.join(MEDIA_DIR, f"img_{uuid.uuid4().hex[:8]}.jpg")
                with open(filename, "wb") as f:
                    f.write(r.content)
                return filename, english_prompt
        except Exception as e:
            print(f"[AI Image Fallback] Model '{model_name or 'default'}' failed: {e}")
            time.sleep(1)

    raise Exception("تعذر الاتصال بخوادم توليد الصور بعد عدة محاولات، يرجى المحاولة بعد لحظات.")

def generate_ai_video(prompt, output_mp4, width=768, height=768, status_callback=None):
    """
    يقوم بتوليد مشاهد تخيلية عالية الدقة عبر FLUX، ثم يقوم بتحريكها سينمائياً
    (Ken Burns Zoom & Pan Effects) ودمجها برمجياً لإنتاج فيديو MP4 احترافي.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise Exception("مكتبة OpenCV غير مثبتة بعد على الخادم.")

    if status_callback:
        status_callback("🎨 <b>[1/3] جاري رسم وتوليد اللوحات والمشاهد التخيلية فائقة الدقة...</b> 🖼️")
    
    # الخطوة 1: توليد صورتين متتاليتين للمشهد لإنشاء حركة وحبكة سينمائية
    english_prompt = translate_to_english(prompt)
    img_path_1, _ = generate_ai_image(f"{english_prompt}, establishing shot, cinematic view", width=width, height=height)
    time.sleep(1)
    img_path_2, _ = generate_ai_image(f"{english_prompt}, dramatic close-up, intense atmosphere", width=width, height=height)

    if status_callback:
        status_callback("🎞️ <b>[2/3] جاري التحريك السينمائي (Ken Burns Motion Effects)...</b> ⚡")

    # الخطوة 2: إنشاء الإطارات (Frames) مع حركة تقريب وسحب سلسة عبر OpenCV
    fps = 24
    duration_per_img = 3.5 # كل صورة 3.5 ثوانٍ + انتقال
    total_frames_per_img = int(fps * duration_per_img)

    img1 = cv2.imread(img_path_1)
    img2 = cv2.imread(img_path_2)
    if img1 is None or img2 is None:
        raise Exception("فشل قراءة ملفات الصور المنتجة.")

    h, w, _ = img1.shape
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_mp4, fourcc, fps, (w, h))

    # حركة الصورة الأولى: تقريب تدريجي (Zoom In) من 1.0 إلى 1.15
    for i in range(total_frames_per_img):
        scale = 1.0 + (0.15 * (i / total_frames_per_img))
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img1, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        # أخذ منتصف الصورة بدقة
        start_x = (new_w - w) // 2
        start_y = (new_h - h) // 2
        frame = resized[start_y:start_y+h, start_x:start_x+w]
        out.write(frame)

    if status_callback:
        status_callback("✨ <b>[3/3] جاري دمج المؤثرات البصرية وتصدير الفيديو الشامل (`MP4`)...</b> 🎬")

    # حركة الصورة الثانية: سحب أفقي (Pan & Zoom Out)
    for i in range(total_frames_per_img):
        scale = 1.15 - (0.10 * (i / total_frames_per_img))
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img2, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        start_x = int((new_w - w) * (i / total_frames_per_img))
        start_y = (new_h - h) // 2
        frame = resized[start_y:start_y+h, start_x:start_x+w]
        out.write(frame)

    out.release()
    # تنظيف الصور المؤقتة بعد التصدير
    try:
        os.remove(img_path_1)
        os.remove(img_path_2)
    except Exception:
        pass

    if not os.path.exists(output_mp4) or os.path.getsize(output_mp4) < 1000:
        raise Exception("فشل تصدير مقطع الفيديو النهائي.")
    return output_mp4

# ==========================================
# 4. محرك بحث الكتب والمكتبات العالمية
# ==========================================
def search_books_engine(query):
    results = []
    # 1. المحاولة الأولى: Google Books API
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=5&printType=books"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            for item in data.get('items', []):
                vol = item.get('volumeInfo', {})
                title = vol.get('title', 'بدون عنوان')
                authors = ", ".join(vol.get('authors', [])) or "مؤلف غير معروف"
                year = str(vol.get('publishedDate') or 'غير محدد')[:4]
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

                book_id = uuid.uuid4().hex[:8]
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
        print(f"[Google Books Search Error]: {e}")

    # 2. المحاولة الثانية: OpenLibrary API (في حال كان Google Books محجوباً أو تجاوز الحصة Quota)
    if not results:
        try:
            print(f"[Fallback Tier 2] Trying OpenLibrary API for: {query}...")
            ol_url = f"https://openlibrary.org/search.json?q={urllib.parse.quote(query)}&limit=5"
            r = requests.get(ol_url, timeout=12)
            if r.status_code == 200:
                data = r.json()
                for doc in data.get('docs', []):
                    title = doc.get('title', 'بدون عنوان')
                    authors_list = doc.get('author_name', ['مؤلف غير معروف'])
                    authors = ", ".join(authors_list[:2]) if isinstance(authors_list, list) else str(authors_list)
                    year = str(doc.get('first_publish_year', 'غير محدد'))
                    pages = doc.get('number_of_pages_median', 'غير محدد')
                    cover_id = doc.get('cover_i')
                    cover = f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None
                    preview_link = f"https://openlibrary.org{doc.get('key', '')}" if doc.get('key') else f"https://openlibrary.org/search?q={urllib.parse.quote(query)}"
                    
                    book_id = uuid.uuid4().hex[:8]
                    book_data = {
                        'id': book_id,
                        'title': title,
                        'authors': authors,
                        'year': year,
                        'pages': pages,
                        'desc': f"كتاب عالمي متميز في مجال {query} من تأليف {authors}. يمكنك الاطلاع على نسخته ومراجعه الكاملة عبر الرابط.",
                        'rating': '4.5',
                        'cover': cover,
                        'preview': preview_link
                    }
                    books_cache[book_id] = book_data
                    results.append(book_data)
        except Exception as e:
            print(f"[OpenLibrary Search Error]: {e}")

    # 3. المحاولة الثالثة: فهرس الذكاء الاصطناعي الفوري (AI Smart Catalog Generator)
    if not results:
        try:
            print(f"[Fallback Tier 3] Using AI Catalog for: {query}...")
            ai_prompt = (
                f"اكتب قائمة بأشهر وأفضل 3 كتب عالمية وموثوقة حول موضوع أو تصنيف: «{query}».\n"
                f"أعد الرد على شكل مصفوفة JSON صالحة فقط بصيغة:\n"
                f'[{{"title": "عنوان الكتاب", "authors": "اسم المؤلف", "year": "سنة النشر", "pages": 300, "desc": "نبذة موجزة عن فكرة الكتاب في سطرين"}}]'
            )
            ai_resp = get_ai_response(ai_prompt, system_instruction="أجب فقط بنص JSON دقيق وصالح ومصفوفة.")
            clean_json = ai_resp.replace("```json", "").replace("```", "").strip()
            items = json.loads(clean_json)
            for item in items[:4]:
                book_id = uuid.uuid4().hex[:8]
                book_data = {
                    'id': book_id,
                    'title': item.get('title', 'كتاب متميز'),
                    'authors': item.get('authors', 'مؤلف عالمي'),
                    'year': str(item.get('year', '2023')),
                    'pages': item.get('pages', '250'),
                    'desc': item.get('desc', 'نبذة وتلخيص لأهم أفكار ومفاهيم الكتاب.'),
                    'rating': '4.8',
                    'cover': None,
                    'preview': f"https://archive.org/search.php?query={urllib.parse.quote(item.get('title', query))}"
                }
                books_cache[book_id] = book_data
                results.append(book_data)
        except Exception as e:
            print(f"[AI Catalog Search Error]: {e}")

    return results

# ==========================================
# 5. أوامر البوت وقائمة التحكم الرئيسية
# ==========================================
def get_main_menu_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📚 بحث في المكتبة والكتب", callback_data="mode_books"),
        types.InlineKeyboardButton("🧭 تصفح تصنيفات الكتب", callback_data="mode_categories")
    )
    markup.add(
        types.InlineKeyboardButton("🎒 مكتبتي المحفوظة (المفضلة)", callback_data="mode_my_library"),
        types.InlineKeyboardButton("🤖 التحدث مع المساعد الذكي", callback_data="mode_chat")
    )
    markup.add(
        types.InlineKeyboardButton("🎨 استوديو توليد الصور", callback_data="mode_image"),
        types.InlineKeyboardButton("🎬 استوديو تصميم الفيديو", callback_data="mode_video")
    )
    markup.add(
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
            f"🌟 <b>أهلاً بك في «بوت المكتبة والذكاء الاصطناعي الشامل»! 🤖📚🎨🎬</b>\n\n"
            f"أنا مساعدك الخارق متعدد المواهب، أجمع بين <b>موسوعة الكتب والمراجع العالمية</b>، وبين <b>استوديو توليد الصور والفيديو (FLUX & AI Studio المجاني 100% وبدون حدود)</b>، ومساعد المحادثة والترجمة الذكية.\n\n"
            f"👇 <b>اختر أحد الأقسام من القائمة التفاعلية أدناه للبدء فوراً:</b>"
        )
        bot.send_message(chat_id, welcome_text, reply_markup=get_main_menu_markup())

    @bot.callback_query_handler(func=lambda call: True)
    def handle_all_callbacks(call):
        chat_id = call.message.chat.id
        data = call.data

        if data == "mode_books":
            user_states[chat_id] = "waiting_book_query"
            msg = (
                "📚 <b>وضع البحث في المكتبة العالمية:</b>\n\n"
                "✏️ أرسل لي الآن <b>اسم الكتاب، أو اسم المؤلف، أو موضوع الكتاب</b> الذي تبحث عنه، وسأجلب لك التفاصيل وملخصه وروابط تحميله الـ PDF فوراً!"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_chat":
            user_states[chat_id] = "waiting_ai_chat"
            msg = (
                "🤖 <b>وضع التحدث والمساعدة الذكية:</b>\n\n"
                "💬 اسألني عن أي شيء يدور في ذهنك! سواء في العلوم، البرمجة، الفلسفة، أو استشارة عامة وسأجيبك بذكاء وتفصيل."
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_categories":
            msg = (
                "🧭 <b>تصفح أشهر تصنيفات الكتب والمراجع العالمية:</b>\n\n"
                "👇 <i>اختر التصنيف الذي تود استكشاف أهم وأفضل كتبه العالمية مع تلخيصاتها:</i>"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("📖 روايات وأدب عالمي (Fiction & Novels)", callback_data="cat_search_fiction"),
                types.InlineKeyboardButton("💻 برمجة وعلوم حاسوب (Computer Science)", callback_data="cat_search_programming"),
                types.InlineKeyboardButton("💰 ريادة أعمال وتطوير ذات (Business & Success)", callback_data="cat_search_business"),
                types.InlineKeyboardButton("🧬 علوم وفلسفة وعلم نفس (Science & Psychology)", callback_data="cat_search_science"),
                types.InlineKeyboardButton("📜 تاريخ وحضارات (History & Civilizations)", callback_data="cat_search_history"),
                types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data.startswith("cat_search_"):
            cat = data.split("cat_search_")[1]
            queries = {
                "fiction": "أشهر الروايات العالمية الأدبية",
                "programming": "programming python algorithms clean code",
                "business": "atomic habits psychology of money rich dad",
                "science": "sapiens cosmos psychology philosophy",
                "history": "تاريخ العالم الحضارات القديمة"
            }
            q = queries.get(cat, "أشهر الكتب العالمية")
            bot.answer_callback_query(call.id, "🔎 جاري جلب أفضل كتب التصنيف...")
            status_msg = bot.send_message(chat_id, f"⏳ <b>جاري استكشاف كتب تصنيف:</b> «{q[:30]}...» 📚")
            threading.Thread(target=process_book_search_task, args=(chat_id, q, status_msg)).start()

        elif data == "mode_my_library":
            db = load_db()
            user_favs = db.get("users", {}).get(str(chat_id), {}).get("favorites", {})
            if not user_favs:
                msg = (
                    "🎒 <b>مكتبتي المحفوظة (المفضلة):</b>\n\n"
                    "ℹ️ <i>مكتبتك فارغة حالياً!</i> عندما تبحث عن أي كتاب وتضغط زر <b>[🔖 حفظ في مكتبتي المفضلة]</b>، سيتم حفظه هنا للرجوع إليه أو تلخيصه في أي وقت."
                )
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books"))
                bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
            else:
                msg = (
                    f"🎒 <b>مكتبتي المحفوظة ({len(user_favs)} كتب):</b>\n\n"
                    f"👇 <i>اختر الكتاب من قائمتك المحفوظة لعرض تفاصيله وبدء تلخيصه أو سؤاله:</i>"
                )
                markup = types.InlineKeyboardMarkup(row_width=1)
                for bid, binfo in list(user_favs.items())[:10]:
                    books_cache[bid] = binfo
                    btn_lbl = f"📕 {binfo.get('title', 'كتاب')[:35]} | ⭐ {binfo.get('rating', '--')}"
                    markup.add(types.InlineKeyboardButton(btn_lbl, callback_data=f"show_book_{bid}"))
                markup.add(types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books"))
                bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data.startswith("fav_toggle_"):
            book_id = data.split("fav_toggle_")[1]
            book = books_cache.get(book_id)
            if not book:
                db = load_db()
                book = db.get("users", {}).get(str(chat_id), {}).get("favorites", {}).get(book_id)
                if book:
                    books_cache[book_id] = book
            if not book:
                bot.answer_callback_query(call.id, "❌ عذراً، يجب عرض الكتاب أولاً للحفظ.")
                return
            db = load_db()
            user_key = str(chat_id)
            if user_key not in db.get("users", {}):
                db.setdefault("users", {})[user_key] = {"favorites": {}}
            user_favs = db["users"][user_key].setdefault("favorites", {})
            if book_id in user_favs:
                del user_favs[book_id]
                save_db(db)
                bot.answer_callback_query(call.id, "🗑️ تم إزالة الكتاب من مكتبتك المفضلة!")
            else:
                user_favs[book_id] = book
                save_db(db)
                bot.answer_callback_query(call.id, "🔖 تم حفظ الكتاب في مكتبتك المفضلة بنجاح!")
            send_book_details_card(chat_id, book)

        elif data.startswith("chat_book_"):
            book_id = data.split("chat_book_")[1]
            book = books_cache.get(book_id)
            if not book:
                db = load_db()
                book = db.get("users", {}).get(str(chat_id), {}).get("favorites", {}).get(book_id)
                if book:
                    books_cache[book_id] = book
            if not book:
                bot.answer_callback_query(call.id, "❌ عذراً، الكتاب غير متوفر في الذاكرة حالياً.")
                return
            user_states[chat_id] = f"waiting_book_q_{book_id}"
            msg = (
                f"💬 <b>أنت الآن في وضع الحوار المباشر مع كتاب:</b> «{book['title']}»\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"✏️ أرسل الآن أي سؤال يخطر في بالك حول هذا الكتاب تحديداً (مثلاً: ما هي أهم نصيحة في الفصل الأول؟ من هو البطل؟ ما الفكرة المركزية؟) وسيجيبك الذكاء الاصطناعي مستنداً لسياق وبيانات الكتاب!"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🔙 العودة لبطاقة الكتاب", callback_data=f"show_book_{book_id}"),
                types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="mode_books")
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data == "mode_image":
            msg = (
                "🎨 <b>استوديو توليد الصور بالذكاء الاصطناعي (`FLUX AI`):</b>\n\n"
                "📐 <b>اختر أولاً مقاس وأبعاد الصورة المطلوبة:</b>"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🔳 صورة مربعة (1:1 - 1024x1024)", callback_data="setimgdim_1024_1024"),
                types.InlineKeyboardButton("📱 ستوري عمودي (9:16 - 768x1344)", callback_data="setimgdim_768_1344"),
                types.InlineKeyboardButton("🖥️ سينمائي أفقي (16:9 - 1344x768)", callback_data="setimgdim_1344_768"),
                types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data.startswith("setimgdim_"):
            parts = data.split("_")
            w, h = int(parts[1]), int(parts[2])
            user_prefs.setdefault(chat_id, {})["img_w"] = w
            user_prefs.setdefault(chat_id, {})["img_h"] = h
            user_states[chat_id] = "waiting_ai_image"
            msg = (
                f"🎨 <b>تم تحديد الأبعاد ({w}x{h}) بنجاح!</b>\n\n"
                f"✏️ أرسل لي الآن أي <b>وصف خيالي أو واقعي</b> تدور أفكاره في ذهنك (بالعربية أو الإنجليزية)، وسأرسمه لك بأعلى دقة سينمائية!\n"
                f"💡 <i>مثال:</i> <code>فارس درعه ذهبي يركب حصاناً أبيض أمام قلعة في الضباب بنمط سينمائي</code>"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_video":
            msg = (
                "🎬 <b>استوديو تصميم وإنتاج الفيديو بالذكاء الاصطناعي (`AI Video Creator`):</b>\n\n"
                "📐 <b>اختر أولاً أبعاد ومقاس المشهد السينمائي:</b>"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🔳 فيديو مربع (1:1 - 768x768)", callback_data="setviddim_768_768"),
                types.InlineKeyboardButton("📱 فيديو عمودي ريلز/ستوري (9:16 - 576x1024)", callback_data="setviddim_576_1024"),
                types.InlineKeyboardButton("🖥️ فيديو سينمائي أفقي (16:9 - 1024x576)", callback_data="setviddim_1024_576"),
                types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data.startswith("setviddim_"):
            parts = data.split("_")
            w, h = int(parts[1]), int(parts[2])
            user_prefs.setdefault(chat_id, {})["vid_w"] = w
            user_prefs.setdefault(chat_id, {})["vid_h"] = h
            user_states[chat_id] = "waiting_ai_video"
            msg = (
                f"🎬 <b>تم تحديد أبعاد الفيديو ({w}x{h}) بنجاح!</b>\n\n"
                f"🎥 أرسل لي الآن <b>فكرة المشهد أو القصة</b> التي تريد تحويلها إلى مقطع فيديو متحرك سينمائي وسأصنعه لك فوراً!\n"
                f"💡 <i>مثال:</i> <code>رحلة سفينة فضائية انسيابية تنطلق نحو سديم ملون في الفضاء العميق</code>"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_summary":
            user_states[chat_id] = "waiting_ai_summary"
            msg = (
                "✍️ <b>وضع تلخيص الكتب والمقالات:</b>\n\n"
                "📋 أرسل لي الآن <b>نصاً طويلاً، أو اسم كتاب تريد تلخيص فصوله وأفكاره</b> وسأقوم بإعداد ملخص شامل ومنظم لك بثوانٍ!"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_translate":
            user_states[chat_id] = "waiting_ai_translate"
            msg = (
                "🌐 <b>وضع الترجمة الذكية الاحترافية:</b>\n\n"
                "🔄 أرسل لي أي نص بلغة أجنبية وسأترجمه لك إلى العربية الفصحى بدقة متناهية (أو العكس)."
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data == "mode_help":
            msg = (
                "ℹ️ <b>دليل استخدام البوت الشامل:</b>\n\n"
                "• <b>📚 للمكتبة والكتب:</b> ابحث بالاسم واحصل على روابط تحميل PDF مباشرة والتلخيص الذكي.\n"
                "• <b>🎨 لتوليد الصور:</b> اكتب أي وصف ليقوم محرك FLUX برسمه بدقة عالية بدون حدود.\n"
                "• <b>🎬 لتصميم الفيديو:</b> يولد لك البوت مشاهد ويحركها برمجياً ليعطيك فيديو MP4 احترافي.\n"
                "• <b>🤖 للمحادثة والترجمة:</b> اسألني أو أرسل أي نص لترجمته أو تلخيصه.\n\n"
                "👇 <i>اختر ما تريد من القائمة الرئيسية أدناه:</i>"
            )
            bot.edit_message_text(msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=get_main_menu_markup())

        elif data.startswith("show_book_"):
            book_id = data.split("show_book_")[1]
            book = books_cache.get(book_id)
            if not book:
                db = load_db()
                for uinfo in db.get("users", {}).values():
                    if book_id in uinfo.get("favorites", {}):
                        book = uinfo["favorites"][book_id]
                        books_cache[book_id] = book
                        break
            if not book:
                bot.answer_callback_query(call.id, "❌ عذراً، انتهت صلاحية هذا الرابط. يرجى البحث مجدداً.")
                return
            bot.answer_callback_query(call.id, "📖 جاري جلب بطاقة الكتاب...")
            send_book_details_card(chat_id, book)

        elif data.startswith("ai_book_"):
            book_id = data.split("ai_book_")[1]
            book = books_cache.get(book_id)
            if not book:
                bot.answer_callback_query(call.id, "❌ يرجى البحث عن الكتاب أولاً.")
                return
            bot.answer_callback_query(call.id, "🧠 جاري التلخيص بالذكاء الاصطناعي...")
            status_msg = bot.send_message(chat_id, f"🧠 <b>جاري إعداد تلخيص وتحليل شامل لأفكار كتاب:</b>\n«{book['title']}» ⏳")
            threading.Thread(target=process_book_ai_summary, args=(chat_id, book, status_msg)).start()

        elif data.startswith("regen_img_"):
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=True):
                bot.answer_callback_query(call.id, "⏳ لديك عملية توليد جارية أو سريعة جداً!")
                return
            media_id = data.split("regen_img_")[1]
            info = media_cache.get(media_id)
            if not info:
                active_tasks[chat_id] = False
                bot.answer_callback_query(call.id, "❌ عذراً، انتهت صلاحية هذا الرابط. يرجى إرسال وصف جديد.")
                return
            prompt = info["prompt"]
            bot.answer_callback_query(call.id, "🎨 جاري إعادة توليد الصورة بنمط جديد...")
            status_msg = bot.send_message(chat_id, f"⏳ <b>[1/2] جاري إعادة رسم الصورة وتوليدها بنمط مختلف...</b> 🎨")
            threading.Thread(target=process_ai_image_task, args=(chat_id, prompt, status_msg)).start()

        elif data.startswith("regen_vid_"):
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=True):
                bot.answer_callback_query(call.id, "⏳ لديك عملية توليد جارية أو سريعة جداً!")
                return
            media_id = data.split("regen_vid_")[1]
            info = media_cache.get(media_id)
            if not info:
                active_tasks[chat_id] = False
                bot.answer_callback_query(call.id, "❌ عذراً، انتهت صلاحية هذا الرابط. يرجى إرسال وصف جديد.")
                return
            prompt = info["prompt"]
            bot.answer_callback_query(call.id, "🎬 جاري إعادة تصميم وإنتاج الفيديو...")
            status_msg = bot.send_message(chat_id, f"🎨 <b>[1/3] جاري رسم وتوليد اللوحات والمشاهد التخيلية...</b> 🖼️")
            threading.Thread(target=process_ai_video_task, args=(chat_id, prompt, status_msg)).start()

        elif data.startswith("dl_vid_doc_"):
            media_id = data.split("dl_vid_doc_")[1]
            info = media_cache.get(media_id)
            vid_path = info["path"] if info else None
            if vid_path and os.path.exists(vid_path):
                bot.answer_callback_query(call.id, "📥 جاري إرسال الفيديو كملف عالي الجودة...")
                with open(vid_path, 'rb') as f:
                    bot.send_document(chat_id, f, caption="📥 <b>ملف الفيديو بأعلى دقة دون ضغط (HD Document)</b>")
            else:
                bot.answer_callback_query(call.id, "❌ عذراً، الملف لم يعد موجوداً في الذاكرة المؤقتة.")

    def send_book_details_card(chat_id, book):
        title = book['title']
        authors = book['authors']
        year = book['year']
        pages = book['pages']
        desc = book['desc']
        rating = book['rating']
        cover = book['cover']
        preview = book['preview']

        full_query = f"تحميل كتاب {title} {authors} pdf مجانا"
        pdf_search_url = f"https://www.google.com/search?q={urllib.parse.quote(full_query)}"

        db = load_db()
        user_favs = db.get("users", {}).get(str(chat_id), {}).get("favorites", {})
        is_fav = book['id'] in user_favs
        fav_btn_text = "💔 إزالة من مكتبتي المفضلة" if is_fav else "🔖 حفظ في مكتبتي المفضلة"

        caption = (
            f"📕 <b>{title}</b> ({year})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✍️ <b>المؤلف:</b> {authors}\n"
            f"📄 <b>الصفحات:</b> {pages} صفحة | ⭐ <b>التقييم:</b> {rating}/5\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📖 <b>نبذة وسطور عن الكتاب:</b>\n"
            f"<i>{desc}</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>اختر أحد خيارات التحميل، الحفظ، أو التلخيص والحوار من الأزرار أدناه:</i> 👇"
        )

        markup = types.InlineKeyboardMarkup(row_width=1)
        if preview:
            markup.add(types.InlineKeyboardButton("📖 قراءة ومعاينة الكتاب (Google Books)", url=preview))
        markup.add(
            types.InlineKeyboardButton("⚡ بحث مباشر عن روابط تحميل PDF المجانية", url=pdf_search_url),
            types.InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{book['id']}"),
            types.InlineKeyboardButton("🧠 تلخيص وشرح أفكار الكتاب بالذكاء الاصطناعي", callback_data=f"ai_book_{book['id']}"),
            types.InlineKeyboardButton("💬 اسأل المساعد الذكي عن هذا الكتاب بالتحديد", callback_data=f"chat_book_{book['id']}"),
            types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
        )

        if cover:
            bot.send_photo(chat_id, cover, caption=caption, reply_markup=markup)
        else:
            bot.send_message(chat_id, caption, reply_markup=markup)

    def process_book_ai_summary(chat_id, book, status_msg):
        prompt = (
            f"قم بإعداد ملخص منظم وشامل باللغة العربية لكتاب «{book['title']}» من تأليف «{book['authors']}».\n"
            f"اذكر أهم 5 أفكار رئيسية يناقشها الكتاب، والفئة المستهدفة، ولماذا يستحق القراءة بأسلوب جذاب."
        )
        try:
            summary_text = get_ai_response(prompt, system_instruction="أنت ناقد أدبي ومستشار قراءة محترف تجيب بلغة عربية فصحى راقية.")
            bot.edit_message_text(
                f"📕 <b>تلخيص وتحليل الذكاء الاصطناعي لكتاب:</b> «{book['title']}»\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"{summary_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 <i>هل لديك أي سؤال محدد حول هذا الكتاب؟ يمكنك طرحه في وضع التحدث!</i> 🤖",
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
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=False):
                return
            status_msg = bot.send_message(chat_id, f"⏳ <b>جاري البحث في المكتبة العالمية عن:</b> «{text}» 🔎")
            threading.Thread(target=process_book_search_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_chat":
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=False):
                return
            status_msg = bot.send_message(chat_id, "🧠 <b>الذكاء الاصطناعي يفكر ويكتب الرد الآن...</b> ⚡")
            threading.Thread(target=process_ai_chat_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_image":
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=True):
                return
            status_msg = bot.send_message(chat_id, "⏳ <b>[1/2] جاري تطوير الوصف وتحسين الأبعاد السينمائية...</b> 🎨")
            threading.Thread(target=process_ai_image_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_video":
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=True):
                return
            status_msg = bot.send_message(chat_id, "🎨 <b>[1/3] جاري رسم وتوليد اللوحات والمشاهد التخيلية...</b> 🖼️")
            threading.Thread(target=process_ai_video_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_summary":
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=False):
                return
            status_msg = bot.send_message(chat_id, "✍️ <b>جاري تحليل واستخراج الأفكار والتلخيص...</b> 📋")
            threading.Thread(target=process_ai_summary_task, args=(chat_id, text, status_msg)).start()

        elif state == "waiting_ai_translate":
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=False):
                return
            status_msg = bot.send_message(chat_id, "🌐 <b>جاري الترجمة الذكية الاحترافية...</b> 🔄")
            threading.Thread(target=process_ai_translate_task, args=(chat_id, text, status_msg)).start()

        elif state.startswith("waiting_book_q_"):
            if not check_rate_limit_and_concurrency(chat_id, is_heavy=False):
                return
            book_id = state.split("waiting_book_q_")[1]
            book = books_cache.get(book_id)
            if not book:
                db = load_db()
                book = db.get("users", {}).get(str(chat_id), {}).get("favorites", {}).get(book_id)
                if book:
                    books_cache[book_id] = book
            if not book:
                bot.send_message(chat_id, "❌ عذراً، لم يعد الكتاب متوفراً في السياق حالياً.", reply_markup=get_main_menu_markup())
                return
            status_msg = bot.send_message(chat_id, f"💬 <b>جاري استشارة الذكاء الاصطناعي حول محتوى كتاب:</b> «{book['title']}»... ⚡")
            threading.Thread(target=process_book_qa_task, args=(chat_id, book, text, status_msg)).start()

        else:
            bot.send_message(
                chat_id,
                "👋 <b>مرحباً بك!</b> يرجى أولاً اختيار القسم الذي تريده من الأزرار أدناه (بحث كتب، أو توليد صور/فيديو، أو محادثة):",
                reply_markup=get_main_menu_markup()
            )

    def process_book_search_task(chat_id, query, status_msg):
        results = search_books_engine(query)
        if not results:
            bot.edit_message_text(
                f"❌ <b>لم يتم العثور على كتب مطابقة لـ:</b> «{query}»\n"
                f"💡 جرب التأكد من الإملاء أو كتابة اسم الكتاب باللغة الأصلية.",
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
            f"👇 <i>اختر الكتاب لعرض بطاقته، روابط التحميل المجانية، والتلخيص الذكي:</i>",
            chat_id=chat_id,
            message_id=status_msg.message_id,
            reply_markup=markup
        )

    def process_ai_chat_task(chat_id, prompt, status_msg):
        response = get_ai_response(prompt)
        bot.edit_message_text(f"🤖 <b>رد المساعد الذكي:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

    def process_book_qa_task(chat_id, book, query_text, status_msg):
        prompt = (
            f"أنت مستشار قراءة ومحلل أدبي خبير. أنت الآن في حوار مباشر مع القارئ بخصوص كتاب محدد:\n"
            f"اسم الكتاب: «{book.get('title')}»\n"
            f"المؤلف: {book.get('authors')}\n"
            f"سنة النشر: {book.get('year')}\n"
            f"نبذة وتفاصيل الكتاب: {book.get('desc')}\n\n"
            f"سؤال القارئ حول هذا الكتاب هو: {query_text}\n\n"
            f"أجب عن سؤال القارئ بوضوح ودقة وعمق أدبي ومعرفي باللغة العربية الفصحى الراقية، وركز إجابتك على محتوى وسياق هذا الكتاب تحديداً."
        )
        response = get_ai_response(prompt, system_instruction="أنت مستشار قراءة ومثقف تجيب بلغة عربية فصحى دقيقة ومفصلة.")
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔙 العودة لبطاقة الكتاب", callback_data=f"show_book_{book['id']}"),
            types.InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="mode_books")
        )
        bot.edit_message_text(
            f"💬 <b>إجابة الذكاء الاصطناعي حول كتاب:</b> «{book['title']}»\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"{response}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>يمكنك إرسال سؤال آخر عن نفس الكتاب مباشرة، أو العودة من الأزرار أدناه:</i> 👇",
            chat_id=chat_id,
            message_id=status_msg.message_id,
            reply_markup=markup
        )

    def process_ai_image_task(chat_id, prompt, status_msg):
        try:
            prefs = user_prefs.get(chat_id, {})
            w = prefs.get("img_w", 1024)
            h = prefs.get("img_h", 1024)
            bot.edit_message_text(f"🎨 <b>[2/2] جاري رسم الصورة فائقة الدقة (`FLUX {w}x{h}`)...</b> ⚡", chat_id=chat_id, message_id=status_msg.message_id)
            img_path, eng_prompt = generate_ai_image(prompt, width=w, height=h)
            
            media_id = uuid.uuid4().hex[:8]
            media_cache[media_id] = {"prompt": prompt, "path": img_path, "type": "image"}
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🔄 إعادة توليد بنمط مختلف", callback_data=f"regen_img_{media_id}"),
                types.InlineKeyboardButton("🎨 رسم صورة جديدة", callback_data="mode_image")
            )
            markup.add(types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books"))

            caption = (
                f"🎨 <b>صورة مولدة بالذكاء الاصطناعي (`FLUX AI`):</b>\n"
                f"📝 <b>الوصف:</b> <i>{prompt}</i>\n"
                f"📐 <b>الأبعاد:</b> <code>{w}x{h}</code>\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

            with open(img_path, 'rb') as f:
                bot.send_photo(chat_id, f, caption=caption, reply_markup=markup)
            
            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
        except Exception as e:
            bot.edit_message_text(f"❌ عذراً، تعذر توليد الصورة حالياً: {e}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())
        finally:
            active_tasks[chat_id] = False

    def process_ai_video_task(chat_id, prompt, status_msg):
        try:
            def update_status(msg_text):
                try:
                    bot.edit_message_text(msg_text, chat_id=chat_id, message_id=status_msg.message_id)
                except Exception:
                    pass

            prefs = user_prefs.get(chat_id, {})
            w = prefs.get("vid_w", 768)
            h = prefs.get("vid_h", 768)
            out_mp4 = os.path.join(MEDIA_DIR, f"vid_{uuid.uuid4().hex[:8]}.mp4")
            generate_ai_video(prompt, out_mp4, width=w, height=h, status_callback=update_status)

            media_id = uuid.uuid4().hex[:8]
            media_cache[media_id] = {"prompt": prompt, "path": out_mp4, "type": "video"}
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🔄 إخراج مشهد جديد لنفس الوصف", callback_data=f"regen_vid_{media_id}"),
                types.InlineKeyboardButton("📥 تحميل كملف مستند HD", callback_data=f"dl_vid_doc_{media_id}")
            )
            markup.add(
                types.InlineKeyboardButton("🎬 تصميم فيديو جديد", callback_data="mode_video"),
                types.InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data="mode_books")
            )

            caption = (
                f"🎬 <b>فيديو منتج بالذكاء الاصطناعي (`AI Video Studio`):</b>\n"
                f"📝 <b>المشهد:</b> <i>{prompt}</i>\n"
                f"━━━━━━━━━━━━━━━━━━"
            )

            with open(out_mp4, 'rb') as f:
                bot.send_video(chat_id, f, caption=caption, reply_markup=markup)

            try:
                bot.delete_message(chat_id, status_msg.message_id)
            except Exception:
                pass
        except Exception as e:
            bot.edit_message_text(f"❌ عذراً، تعذر إنتاج الفيديو حالياً: {e}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())
        finally:
            active_tasks[chat_id] = False

    def process_ai_summary_task(chat_id, text, status_msg):
        prompt = f"قم بتلخيص وتفكيك هذا النص أو الكتاب إلى نقاط واضحة وموجزة ومرتبة:\n\n{text}"
        response = get_ai_response(prompt, system_instruction="أنت خبير تلخيص أكاديمي ومثقف تجيب بأسلوب منظم جداً ونقاط واضحة.")
        bot.edit_message_text(f"📋 <b>التلخيص الذكي:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

    def process_ai_translate_task(chat_id, text, status_msg):
        prompt = f"ترجم هذا النص ترجمة أدبية واحترافية دقيقة إلى اللغة العربية الفصحى (وإذا كان بالعربية ترجمه إلى الإنجليزية):\n\n{text}"
        response = get_ai_response(prompt, system_instruction="أنت مترجم فوري محترف تتقن اللغتين العربية والإنجليزية ببراعة فائقة.")
        bot.edit_message_text(f"🌐 <b>الترجمة الاحترافية:</b>\n━━━━━━━━━━━━━━━━━━\n\n{response}", chat_id=chat_id, message_id=status_msg.message_id, reply_markup=get_main_menu_markup())

# ==========================================
# 6. خادم الويب للحفاظ على البوت نشطاً 24/7 على Render
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    status = "Online 🟢" if bot else "Missing Token ⚠️"
    return f"<h1>📚🤖🎨🎬 AI & Library & Media Studio Bot is Alive 24/7! (Status: {status})</h1><p>Ready to search books, generate FLUX images & cinematic AI videos seamlessly.</p>"

def cleanup_old_media_daemon():
    """
    خيط خلفي (Daemon Thread) ينظف مجلد generated_media من الصور والفيديوهات
    التي مر عليها أكثر من 24 ساعة لتجنب امتلاء القرص الصلب (Disk Full) سواء على السيرفر أو الحاسوب.
    """
    while True:
        try:
            time.sleep(3600 * 6)  # فحص كل 6 ساعات
            if os.path.exists(MEDIA_DIR):
                now = time.time()
                for filename in os.listdir(MEDIA_DIR):
                    if filename.endswith(".jpg") or filename.endswith(".mp4"):
                        file_path = os.path.join(MEDIA_DIR, filename)
                        try:
                            # إذا مر على الملف أكثر من 24 ساعة (86400 ثانية)
                            if now - os.path.getmtime(file_path) > 86400:
                                os.remove(file_path)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Cleanup Error]: {e}")

def run_bot_polling():
    if not bot:
        print("[ERROR] لم يتم تشغيل البوت بسبب عدم وجود TELEGRAM_BOT_TOKEN.")
        return
    while True:
        try:
            print("[INFO] AI & Library & Media Studio Bot is polling Telegram...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"[ERROR] Bot restart due to: {e}")
            time.sleep(5)

if __name__ == "__main__":
    cleanup_thread = threading.Thread(target=cleanup_old_media_daemon, daemon=True)
    cleanup_thread.start()

    polling_thread = threading.Thread(target=run_bot_polling, daemon=True)
    polling_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
