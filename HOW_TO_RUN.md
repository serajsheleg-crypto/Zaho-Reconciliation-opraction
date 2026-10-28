# تشغيل Zaho Reconcile يدويًا

هذا الدليل يشرح تشغيل النظام بالأوامر خطوة بخطوة، بدون الاعتماد على `install.bat` و`start.bat` (الملفان يفعلان الشيء نفسه بنقرة مزدوجة).

مسار المشروع على هذا الجهاز: `C:\Users\lenovo\Desktop\Zaho-Reconciliation`

## المتطلبات

| البرنامج | لماذا | ملاحظة |
|---|---|---|
| Python 3.11 أو أحدث | تشغيل الخادم ومحرك المطابقة | المثبت هنا: Python 3.14.5 في `C:\Python314` (فعّل «Add python.exe to PATH» عند التثبيت على جهاز آخر) |
| XAMPP (MySQL على المنفذ 3306) | حفظ المستخدمين والمطابقات والاستثناءات وسير المراجعة | |
| Chrome أو Edge | تصدير تقرير PDF | |
| Node.js 20 أو أحدث | فقط لإعادة بناء الواجهة بعد تعديل كودها | الواجهة مبنية مسبقًا في `frontend\dist` |

## 1. شغّل MySQL

افتح **XAMPP Control Panel** واضغط **Start** بجانب **MySQL**. للتأكد أنه يعمل:

```powershell
netstat -ano | findstr :3306
```

يجب أن يظهر سطر فيه `LISTENING`.

## 2. التثبيت لأول مرة (مرة واحدة فقط)

> على هذا الجهاز تم التثبيت بالفعل (بيئة Python جديدة في `.venv` بتاريخ 27-10-2026، وقاعدة البيانات `zaho_recon` محدّثة). انتقل مباشرة إلى الخطوة 3، وارجع إلى هذه الخطوة فقط على جهاز جديد أو بعد نقل المجلد.

افتح PowerShell ونفّذ:

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.venv\Scripts\python.exe scripts\setup.py
```

ما يفعله `scripts\setup.py`:

- ينشئ ملف `.env` بكلمة سر عشوائية للتطبيق ولقاعدة البيانات (إذا لم يكن موجودًا؛ الملف الموجود لا يُستبدل).
- ينشئ قاعدة البيانات `zaho_recon` ومستخدمًا خاصًا `zaho_recon` صلاحياته على هذه القاعدة فقط (التطبيق لا يعمل بحساب `root`).
- ينشئ الجداول أو يحدّثها، وينشئ حساب المسؤول الأول.

إذا كان لحساب `root` في MySQL كلمة سر:

```powershell
.venv\Scripts\python.exe scripts\setup.py --root-password كلمة_السر
```

لإعادة بناء الواجهة (فقط إذا عدّلت ملفات `frontend\src`):

```powershell
cd frontend
npm install
npm run build
cd ..
```

## 3. تشغيل النظام

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation\backend
..\.venv\Scripts\python.exe -m app.main
```

انتظر حتى يظهر السطر:

```
Uvicorn running on http://127.0.0.1:8077 (Press CTRL+C to quit)
```

ثم افتح المتصفح على **http://127.0.0.1:8077**. أبقِ نافذة PowerShell مفتوحة طوال العمل؛ لإيقاف النظام اضغط `Ctrl+C` فيها أو أغلقها.

> الأمر يجب أن يُنفذ من داخل مجلد `backend` لأن كود الخادم موجود فيه. عند كل تشغيل يحدّث الخادم جداول قاعدة البيانات تلقائيًا إذا كان هناك تحديث جديد، ويعلّم أي مطابقة توقفت بسبب إغلاق سابق على أنها «فشلت».

## 4. تسجيل الدخول

الحساب الأول: **admin@zaho.ly** وكلمة السر **Admin@2026** (إذا لم تُغيَّر بعد). سيطلب النظام تغييرها من صفحة «حسابي». يمكن إضافة مستخدمين آخرين من صفحة «المستخدمون» بالأدوار: مسؤول، محلل، مشاهد.

## 5. أول استخدام مقترح

1. صفحة **الخدمات والحالات** (للمسؤول): أضف الخدمات التي تطابقها وحدد لكل خدمة هل تنطبق عليها مطابقة الرسوم / الإيراد، ثم اعتمد أسماء الحالات التي يكتبها شركاؤك (مثل `Approved` = ناجحة) بعد موافقة الإدارة.
2. صفحة **الشركات والقوالب** (للمسؤول): شركتنا (Zaho) موجودة من البداية. أضف كل شريك (مثل OnePay)، ثم «إضافة قالب» لكل شركة من ملف نموذجي واحد كما ترسله: يتعرف النظام على الأعمدة، راجعها (المرجع، المبلغ، الحالة، التاريخ، الوقت إن كان في عمود مستقل، العملة، الرسوم)، وسمِّ الحقول الأخرى التي تريد مقارنتها (مثل `Direction`؛ تُقارن مع الحقل الذي يحمل الاسم نفسه في قالب الشركة الأخرى)، وحدد معنى كلمات الحالة الخاصة بهذه الشركة (تُعتبر معتمدة لملفها فقط). قالب لنظامنا وقالب لكل شريك.
3. **مطابقة جديدة**: اختر «قالب الشركة» لكل طرف (أو اتركه «بدون قالب» ليتعرف النظام على الأعمدة تلقائيًا) ← ارفع الملفين ← راجع «الحقول» ← اختر الخدمة وطريقة مقارنة الحالة في «القواعد» ← راجع «التحقق» ← ابدأ المطابقة.
4. لتجربة القوالب: مجلد **`Desktop\Recon Template Test Data`** (4 شركات × يومين، مع النتائج المتوقعة وملف README). لإنشاء شركات وقوالب الاختبار كلها مرة واحدة:

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation
.venv\Scripts\python.exe scripts\make_template_samples.py     # يولّد المجلد إن لم يكن موجودًا
.venv\Scripts\python.exe scripts\seed_test_templates.py      # يضيف الشركات والقوالب (لا يكرر الموجود)
```

5. لتجربة سريعة بملفات جاهزة: ملفات العرض في مجلد `samples`، والمثال الصغير المشروح في `docs\algorithm\example`. أو أضف مطابقة عرض جاهزة:

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation
.venv\Scripts\python.exe scripts\demo.py
```

## 6. الإعدادات (`.env`)

الملف في جذر المشروع. أعد تشغيل النظام بعد أي تعديل.

| المفتاح | الافتراضي | المعنى |
|---|---|---|
| `PORT` | 8077 | منفذ الموقع |
| `HOST` | 127.0.0.1 | اجعله `0.0.0.0` ليستخدم النظام أشخاص آخرون على الشبكة (واسمح بالمنفذ في جدار الحماية) |
| `MAX_UPLOAD_MB` | 1024 | أكبر حجم لكل ملف مرفوع |
| `DUCKDB_MEMORY_LIMIT` | 4GB | ذاكرة محرك المطابقة قبل استخدام القرص `data\tmp` |
| `MAX_DB_EXCEPTIONS` | 1000000 | عدد الاستثناءات المحفوظة في MySQL لكل مطابقة (التصدير يحتوي الكل دائمًا) |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | zaho_recon | اتصال قاعدة البيانات |

## 7. تشغيل الاختبارات

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation
.venv\Scripts\python.exe -m pytest backend\tests -q
```

النتيجة المتوقعة: `88 passed`. الاختبارات تستخدم قاعدة بيانات مؤقتة `zaho_recon_test` بحساب `root` ومجلد بيانات مؤقت، ولا تلمس بياناتك. اختبار الواجهة في متصفح حقيقي مع صور لكل شاشة (والنظام يعمل):

```powershell
cd C:\Users\lenovo\Desktop\Zaho-Reconciliation\frontend
node e2e\flow.mjs shots
node e2e\templates.mjs shots-templates    # الشركات والقوالب: إنشاء من ملف نموذجي ثم مطابقة ومقارنة بالنتائج المتوقعة
```

## 8. حل المشكلات

| المشكلة | الحل |
|---|---|
| `Can't connect to MySQL server` أو الخطأ 2003 | MySQL غير مشغّل: الخطوة 1. |
| `Access denied for user 'zaho_recon'` | كلمة السر في `.env` لا تطابق MySQL: نفّذ `.venv\Scripts\python.exe scripts\setup.py` مرة أخرى (يعيد ضبط كلمة سر المستخدم من `.env`). |
| `No module named 'app'` | الأمر نُفّذ من خارج مجلد `backend`: نفّذ `cd backend` أولًا. |
| `address already in use` أو المنفذ 8077 مشغول | النظام يعمل في نافذة أخرى. اعرف البرنامج بـ `netstat -ano \| findstr :8077` وأغلق نافذته، أو غيّر `PORT` في `.env`. |
| `python` غير معروف | استخدم المسار الكامل `C:\Python314\python.exe -m venv .venv`، أو أعد تثبيت Python مع خيار PATH. |
| نقلت المجلد أو غيرت اسمه | بيئة `.venv` مرتبطة بمسارها: احذف مجلد `.venv` وأعد أوامر الخطوة 2 حتى `pip install` (لا حاجة لإعادة `setup.py`). |
| الصفحة فارغة أو تظهر نسخة قديمة | اضغط `Ctrl+F5`. إذا عدّلت كود الواجهة أعد بناءها (الخطوة 2). |
| تصدير PDF يفشل | ثبّت Chrome أو Edge. |
| phpMyAdmin يظهر تحذير «configuration storage» أو MySQL لا يقبل `DROP DATABASE` | جداول نظام MySQL في XAMPP تالفة (حدث على هذا الجهاز سابقًا)؛ لا يؤثر على تشغيل النظام، ويحتاج إصلاحًا من مسؤول قاعدة البيانات. |

## 9. أين توجد الأشياء

```
backend\app\           الخادم (FastAPI) ومحرك المطابقة (engine\)
backend\tests\         الاختبارات
frontend\              الواجهة (React)؛ frontend\dist هي النسخة المبنية التي يعرضها الخادم
scripts\               setup.py، demo.py، make_samples.py، make_big.py،
                       make_template_samples.py (بيانات اختبار القوالب)، seed_test_templates.py (إضافة قوالب الاختبار)
samples\               ملفات العرض التجريبي
data\                  الملفات المرفوعة ونتائج المطابقات والتصديرات (لا تحذفه والنظام يعمل)
docs\algorithm\        شرح خوارزمية المطابقة (Markdown وPDF ومثال عملي)
.env                   الإعدادات وكلمات السر (لا تشاركه)
```
