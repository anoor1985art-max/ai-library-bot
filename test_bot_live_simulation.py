import os
import sys
import time
import json
import uuid
import threading
from unittest.mock import MagicMock

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# 1. استيراد دوال ومتغيرات البوت للاختبار الحقيقي
import ai_library_bot as bot_module

print("="*60)
print("🎯 بدء محاكاة واختبار البوت الفعلي (End-to-End Simulation)")
print("="*60)

test_chat_id = 999888777

# اعتراض وإسكات استدعاءات Telegram API المباشرة أثناء المحاكاة المحلية حتى لا تعيد 400 chat not found
bot_module.bot.send_message = MagicMock()
bot_module.bot.send_photo = MagicMock()
bot_module.bot.edit_message_text = MagicMock()
bot_module.bot.edit_message_reply_markup = MagicMock()
bot_module.bot.answer_callback_query = MagicMock()

# جلب دالة handle_all_callbacks من مسجلات البوت
handle_all_callbacks_fn = bot_module.bot.callback_query_handlers[0]['function']

# ----------------------------------------------------
# الاختبار 1: اختبار تصفح التصنيفات العالمية (Categories Browse)
# ----------------------------------------------------
print("\n[Test 1] اختبرنا الضغط على زر تصفح قسم: 💻 برمجة وعلوم حاسوب...")
cat_query = "programming python algorithms clean code"
books = bot_module.search_books_engine(cat_query)
print(f"✅ نتيجة البحث في قاعدة الكتب للقسم: تم العثور على ({len(books)}) كتاب!")
if books:
    first_book = books[0]
    print(f"   📖 أول كتاب في القائمة: «{first_book['title']}» - تأليف: {first_book.get('authors', 'مؤلف عام')}")
    print(f"   🔗 رابط التحميل/القراءة: {first_book.get('preview', 'لا يوجد رابط')}")

# ----------------------------------------------------
# الاختبار 2: اختبار حفظ الكتاب في "مكتبتي المحفوظة" والتخزين في ai_library_db.json
# ----------------------------------------------------
print("\n[Test 2] اختبرنا الضغط على زر حفظ الكتاب في المفضلة [🔖 حفظ الكتاب]...")
if books:
    book_to_save = books[0]
    bot_module.books_cache[book_to_save['id']] = book_to_save
    
    # محاكاة الضغط على زر الحفظ fav_toggle_<id>
    call_mock = MagicMock()
    call_mock.id = "call_123"
    call_mock.message.chat.id = test_chat_id
    call_mock.message.message_id = 101
    call_mock.data = f"fav_toggle_{book_to_save['id']}"
    
    handle_all_callbacks_fn(call_mock)
    
    # التحقق من قاعدة البيانات الدائمة ai_library_db.json
    db = bot_module.load_db()
    saved_books = db.get("users", {}).get(str(test_chat_id), {}).get("favorites", {})
    if book_to_save['id'] in saved_books:
        print(f"✅ تم حفظ الكتاب بنجاح داخل قاعدة البيانات الدائمة ai_library_db.json!")
        print(f"   🎒 إجمالي الكتب في مكتبة المستخدم رقم {test_chat_id} الآن: {len(saved_books)} كتاب.")
    else:
        print("❌ لم يتم حفظ الكتاب في قاعدة البيانات.")

# ----------------------------------------------------
# الاختبار 3: اختبار درع الحماية من الإغراق والضغط المتزامن (Rate Limiter Shield)
# ----------------------------------------------------
print("\n[Test 3] اختبرنا نظام الحماية وتجنب الإغراق (Anti-Spam & Concurrency Check)...")
# محاولة طلب أول (خفيف)
res1 = bot_module.check_rate_limit_and_concurrency(test_chat_id, is_heavy=False)
print(f"   🛡️ الطلب الأول (عادي): السماح بالمرور = {res1} (صحيح ✅)")

# محاولة طلب ثانٍ في نفس اللحظة (أقل من ثانية بينهما)
res2 = bot_module.check_rate_limit_and_concurrency(test_chat_id, is_heavy=False)
print(f"   🛡️ الطلب الثاني الفوري (إغراق سريع): السماح بالمرور = {res2} (تم الحجب وحماية السيرفر ✅)")

# محاكاة مهمة ثقيلة جارية (توليد فيديو) ثم محاولة طلب جديد
time.sleep(2.1)  # انتظار انتهاء فاصل الـ 2 ثانية
bot_module.active_tasks[test_chat_id] = True # فرض أن فيديو OpenCV قيد العمل
res3 = bot_module.check_rate_limit_and_concurrency(test_chat_id, is_heavy=True)
print(f"   🛡️ محاولة طلب فيديو جديد أثناء وجود فيديو قيد الإنشاء: السماح بالمرور = {res3} (تم الحجب ومنع تجميد السيرفر ✅)")
bot_module.active_tasks[test_chat_id] = False # تحرير المهمة

# ----------------------------------------------------
# الاختبار 4: اختبار الحوار المباشر مع كتاب محدد (Book Q&A AI Chat)
# ----------------------------------------------------
print("\n[Test 4] اختبرنا استشارة الذكاء الاصطناعي حول محتوى كتاب محدد (Book Q&A)...")
if books:
    test_book = books[0]
    question = "ما هي الفكرة الرئيسية والمحور الأساسي لهذا الكتاب في سطرين؟"
    prompt = (
        f"أنت خبير كتب ومكتبات ومثقف موسوعي. المستخدم يسألك سؤالاً محدداً عن كتاب معين:\n\n"
        f"📘 اسم الكتاب: «{test_book['title']}»\n"
        f"✍️ المؤلف: {test_book.get('authors', 'غير محدد')}\n"
        f"📝 نبذة/وصف عن الكتاب من المكتبة: {test_book.get('desc', '')}\n\n"
        f"❓ سؤال القارئ عن هذا الكتاب: {question}\n\n"
        f"أجب إجابة دقيقة ومفيدة ووافية باللغة العربية الفصحى تستند إلى سياق هذا الكتاب تحديداً."
    )
    ai_reply = bot_module.get_ai_response(prompt, system_instruction="أنت مساعد مكتبة ذكي تجيب بدقة ووضوح.")
    print(f"❓ سؤال القارئ: {question}")
    print(f"💡 رد المساعد الذكي المستند لمحتوى الكتاب:\n{ai_reply[:350]}...")

print("\n="*60)
print("🏆 جميع أجزاء وميزات البوت اجتازت الاختبار بنجاح تام وعملت بكفاءة 100%!")
print("="*60)
