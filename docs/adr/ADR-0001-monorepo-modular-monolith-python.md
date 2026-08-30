# ADR-0001: Monorepo، Modular Monolith و Python 3.13

- Status: Accepted
- Date: 2026-08-28
- Work Package: Phase 0 — Architecture baseline
- Decision owner: مالک محصول برای تصمیم اصلی؛ حاکمیت اجرای فعلی با Codex مدیر پروژه طبق Roadmap 0.31

## Context

سامانه از یک Agent ویندوز کنار Excel و چند Process سرور برای API، Webhook و Worker تشکیل می‌شود، اما قواعد حسابداری باید یک‌بار و مستقل از Telegram، دیتابیس و Framework نوشته شوند. این ADR تصمیم مصوب O-52 و بخش 15.6 Roadmap را ثبت می‌کند.

## Constraints

- G0 در 2026-08-30 بسته شده است؛ اجرای این ADR فقط در Work Package محدود و بازبینی‌شده مجاز است.
- موتور Domain باید از چهار شیت Raw مصوب تغذیه و از Formula/Query اکسل مستقل باشد.
- Telegram فقط رابط تحویل است و نباید محل قواعد مالی باشد.
- نسخه اول روی یک سرور مرکزی و یک Agent ویندوز اجرا می‌شود و پیچیدگی عملیاتی باید محدود بماند.

## Options considered

### Option A — Monorepo با Modular Monolith و Python 3.13

- Benefits: یک پیاده‌سازی مشترک برای قواعد مالی، قراردادهای یکسان Agent/Server، تست و Refactor ساده‌تر و امکان جداسازی Service در آینده.
- Costs/risks: مرز ماژول‌ها باید با تست و Review کنترل شود؛ Monorepo بدون انضباط می‌تواند به وابستگی چرخه‌ای تبدیل شود.
- Reversibility: بالا؛ هر Process یا Module بعداً می‌تواند پشت قرارداد موجود جدا شود.

### Option B — Microservice و مخزن جدا از آغاز

- Benefits: استقرار و مقیاس مستقل هر سرویس.
- Costs/risks: قرارداد شبکه، نسخه‌بندی، Observability و عملیات توزیع‌شده زودهنگام؛ هزینه بالا برای حجم نسخه اول.
- Reversibility: متوسط؛ بازگشت از توزیع زودهنگام پرهزینه است.

### Option C — یک Process یکپارچه برای Agent، API و Bot

- Benefits: Scaffold اولیه کوچک‌تر.
- Costs/risks: Windows/Excel را با سرور و Telegram درهم می‌آمیزد و جداسازی موتور محاسبات را نقض می‌کند.
- Reversibility: پایین تا متوسط.

## Recommendation

Option A انتخاب شده است. ساختار پس از G0 شامل `apps/local_agent`، `apps/server_api`، `apps/worker`، `packages/domain`، `packages/contracts`، `packages/persistence`، `packages/reporting`، `infra`، `tests`، `docs/adr` و `handoffs` خواهد بود.

`packages/domain` Python خالص است و نباید به FastAPI، aiogram، SQLAlchemy، Playwright، Excel COM یا Transport وابسته شود. API/Webhook و Workerها Processهای جدا هستند، اما همان Domain و Contractهای نسخه‌دار را مصرف می‌کنند. Agent ویندوز برنامه‌ای مستقل در همان Monorepo است.

## Roadmap and acceptance impact

- مرجع: O-52، بخش 15.6 و معیارهای 19.1 و 19.6.
- پیش از G1 یک Spike باید Python 3.13، Excel COM و PyInstaller را روی Windows هدف اثبات کند.
- CI باید قانون مرز وابستگی Domain را کنترل کند و Rebuild از Raw نتیجه قطعی یکسان بدهد.

## Evidence plan

- G1: Import smoke test پکیج Domain بدون نصب Frameworkهای Transport.
- G1: Spike بسته‌بندی و COM روی کپی مجاز Excel.
- G2: Rebuild کامل Ledger از Raw و برابری نتایج.
- همه Gateها: آزمون جلوگیری از وابستگی ممنوع و چرخه Import.

## Migration and rollback impact

هنوز Migration یا کدی وجود ندارد. اگر یک Module بعداً نیازمند جداسازی شود، قراردادهای `packages/contracts` مرز استخراج خواهند بود؛ تغییر قواعد مالی یا رفتار قابل مشاهده نیازمند تصمیم Roadmap جداگانه است.

## Reconsideration triggers

- نیاز اثبات‌شده به استقرار مستقل یا مقیاس چندسروری؛
- Benchmark یا مرز مالکیت تیمی که Modular Monolith را ناکافی نشان دهد؛
- شکست Spike Python/COM که فقط می‌تواند Bridge کوچک C# را از مسیر ADR جدا مطرح کند.

## Approval required

این تصمیم قبلاً توسط مالک در O-52 تأیید شده و G0 در 2026-08-30 بسته شده است. Scope و پذیرش هر Work Package با Codex مدیر پروژه است؛ اقدام روی دارایی‌های محافظت‌شده همچنان اختیار هدف‌دار مالک را می‌خواهد.
