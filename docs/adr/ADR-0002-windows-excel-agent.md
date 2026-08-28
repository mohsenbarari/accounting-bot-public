# ADR-0002: Agent ویندوز برای Excel، SQLite و Sync

- Status: Accepted
- Date: 2026-08-28
- Work Package: Phase 0 — Architecture baseline
- Decision owner: Product Owner؛ ثبت و بازبینی توسط Codex

## Context

Agent باید Save فایل `.xlsx` را در Session واقعی کاربر تشخیص دهد، UUID را فقط پس از اجازه جداگانه در حافظه زنده Excel مدیریت کند، چهار شیت Raw را بدون وابستگی به Recalculation بخواند و در قطعی طولانی Outbox دیسکی داشته باشد. این ADR تصمیم O-53 را ثبت می‌کند.

## Constraints

- فایل اصلی Excel بدون اجازه صریح و جداگانه تغییر نمی‌کند.
- فایل باید `.xlsx` بماند و VBA داخل Workbook اضافه نشود.
- Office Automation در Windows Service پشتیبانی مطمئنی ندارد؛ Agent باید در Session تعاملی کاربر اجرا شود.
- Snapshot، Import و Outbox باید در قطع اینترنت ادامه یابند و Save بدون تغییر صفر Event بسازد.

## Options considered

### Option A — Agent Python در Session تعاملی

- Benefits: اشتراک Domain/Contract با سرور، دسترسی به `WorkbookBeforeSave` و DPAPI، بسته‌بندی مستقل و SQLite داخلی.
- Costs/risks: COM و PyInstaller به نسخه Office/Windows حساس‌اند و نیازمند Spike واقعی هستند.
- Reversibility: بالا؛ Adapterهای COM، Reader و Sync مرزهای جدا دارند.

### Option B — Windows Service برای Office Automation

- Benefits: اجرای پس‌زمینه مستقل از Login.
- Costs/risks: ناسازگار با مدل تعاملی Office و BeforeSave؛ ریسک Hang و Session isolation.
- Reversibility: متوسط.

### Option C — VBA یا تبدیل فایل به XLSM

- Benefits: دسترسی مستقیم به رویداد Save.
- Costs/risks: تغییر فرمت و کد داخل فایل مرجع، توزیع و اعتماد سخت‌تر و مغایر تصمیم کاربر.
- Reversibility: پایین.

### Option D — Agent کامل C#/.NET

- Benefits: یکپارچگی قوی با Windows و COM.
- Costs/risks: دو هسته زبانی و تکرار قرارداد/قواعد مالی.
- Reversibility: متوسط.

## Recommendation

Option A انتخاب شده است: Python 3.13، `pywin32` برای Excel COM و DPAPI، `watchdog` برای اعلان فایل، `zipfile` و `lxml.iterparse` برای خواندن Streaming ستون‌های Whitelist، `sqlite3` برای Mirror/Outbox، `httpx` برای HTTPS، `cryptography` برای Ed25519، `Pydantic`/`pydantic-settings` برای قرارداد و تنظیمات و `PyInstaller` برای بسته نصب‌پذیر.

Agent از Task Scheduler/Startup در Session تعاملی اجرا می‌شود و Windows Service نیست. COM فقط Adapter رویداد/نوشتن UUID است؛ استخراج Raw از Snapshot ثابت XLSX انجام می‌شود. در صورت شکست اثبات‌شده COM/PyInstaller، فقط Bridge کوچک C# برای رویداد Excel می‌تواند با ADR تازه پیشنهاد شود و Domain Python باقی می‌ماند.

## Roadmap and acceptance impact

- مرجع: O-53، بخش‌های 5.1، 5.2، 15.3 و معیارهای 19.1/19.5.
- Targetهای فعلی: Parse/Hash زیر 15 ثانیه و 128 MiB روی فایل مرجع؛ RAM کمتر از 350 MiB هنگام Import/Sync؛ Outbox پایدار و Idempotent.
- BeforeSave، UUID، Copy/Insert/Sort، Save loop و OneDrive باید روی کپی مجاز اثبات شوند.

## Evidence plan

- G1: Spike `WorkbookBeforeSave`، تشخیص Write داخلی و جلوگیری از حلقه Save.
- G1: حفظ UUID در Edit/Sort و ساخت UUID تازه در Insert/Copy بدون ابهام.
- G1: بسته PyInstaller روی Windows هدف بدون Python نصب‌شده و اجرای پس از Login/Restart.
- G1/G4: Streaming چهار شیت، Snapshot cleanup، SQLite rollback و Outbox در قطعی مصنوعی.
- G4: DPAPI، امضای Ed25519، Retry و تخلیه Backlog بدون Duplicate.

## Migration and rollback impact

Agent می‌تواند بدون تغییر Raw Excel غیرفعال شود و Outbox تأییدنشده نباید حذف شود. Rollback نسخه Agent باید Schema SQLite سازگار را حفظ کند؛ Migration مخرب یا نوشتن Excel خارج از ستون فنی مصوب نیازمند اجازه جداگانه است.

## Reconsideration triggers

- شکست تکرارپذیر COM یا PyInstaller روی محیط هدف؛
- عبور پایدار از بودجه زمان/RAM؛
- تغییر محصول به منبع داده‌ای غیر از Excel.

## Approval required

این تصمیم قبلاً در O-53 تأیید شده است. هیچ Spike نوشتاری روی Excel، حتی کپی، تا اجازه صریح نام‌گذاری‌شده کاربر اجرا نمی‌شود و کدنویسی نهایی منوط به G0 است.
