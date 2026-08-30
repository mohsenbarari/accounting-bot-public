# ADR-0006: قرارداد Canonical ورودی و Hashهای منبع

- Status: Accepted
- Date: 2026-08-30
- Work Package: Phase 1 — WP-03
- Decision owner: Codex Project Manager under Roadmap O-46/O-49

## Context

تشخیص قطعی Insert/Edit/Delete باید روی Agent ویندوز و سرور، مستقل از ترتیب ردیف، ترتیب کلیدهای Mapping و جزئیات Serialization پلتفرم، نتیجه یکسان بدهد. Roadmap در O-03 سه Hash نسخه‌دار SHA-256 را تعیین کرده است، اما بدون قرارداد دقیق بایتی، تغییر نسخه Python یا پیاده‌سازی جداگانه می‌تواند برای یک داده یکسان Hash متفاوت بسازد.

WP-02 مرز ساختاری چهار شیت و ترتیب قطعی ستون‌های ورودی را تثبیت کرده است. تصمیم حاضر قرارداد نخستین نسخه `source_hash` و `sheet_snapshot_hash` و Canonical تاریخ/عدد لازم برای آنها را مشخص می‌کند. `ledger_hash` به دلیل وابستگی به Resolver، Financial Event و قواعد محاسبات فاز 2 در این ADR پیاده‌سازی نمی‌شود.

## Constraints

- فقط ستون‌های Literal Raw ثبت‌شده در `raw-source-contract.v1` وارد `source_hash` می‌شوند؛ شناسه فنی، فرمول، Cached Value، ستون مشتق، ستون ثبت‌نشده و موقعیت ردیف/سلول حذف می‌شوند.
- Float، Boolean به‌جای عدد، NaN و Infinity در مرز Canonical مجاز نیستند.
- مقدار خام تاریخ Excel باید بعداً در Raw Immutable حفظ شود، ولی تاریخ Canonical قابل Query نیز باید با `persiantools` نسخه‌قفل‌شده تولید شود.
- روز مالی فقط از تاریخ ثبت‌شده در Excel می‌آید؛ زمان Save/Import صرفاً Audit است و با `Asia/Tehran` نمایش داده می‌شود.
- تغییر این قرارداد پس از ذخیره Hashهای عملیاتی نباید معنای نسخه 1 را به‌طور خاموش عوض کند.
- این تصمیم مجوز خواندن یا نوشتن Excel مرجع/کپی واقعی، استفاده از داده واقعی یا ساخت Schema پایگاه‌داده نیست.

## Options considered

### Option A — JSON آرایه‌ای تایپ‌دار، نسخه‌دار و دارای Golden Vector

- Benefits: قابل‌خواندن و Debug، مستقل از ترتیب Mapping، قابل‌پیاده‌سازی میان‌زبانی و دارای نمایش بایتی صریح.
- Costs/risks: از قالب Binary کمی بزرگ‌تر است و هر قاعده Escape/Number باید دقیقاً ثابت بماند.
- Reversibility: بالا؛ نسخه تازه کنار نسخه قبلی افزوده می‌شود.

### Option B — JSON Object با `sort_keys`

- Benefits: برای انسان آشناتر است و کد اولیه کوتاه‌تری دارد.
- Costs/risks: اتکا به نام/ترتیب کلید، رفتار کتابخانه و نمایش اعداد بیشتر است؛ Field order مصوب WP-02 نیز در Payload آشکار نمی‌ماند.
- Reversibility: متوسط.

### Option C — قالب Binary یا Length-prefixed اختصاصی

- Benefits: فشرده و از نظر مرز فیلدها صریح است.
- Costs/risks: مشاهده و عیب‌یابی دشوارتر، پیاده‌سازی بیشتر و برای حجم نسخه اول بی‌فایده است.
- Reversibility: متوسط تا پایین.

## Decision

Option A انتخاب شد. Canonical bytes با JSON آرایه‌ای و قواعد زیر ساخته می‌شوند:

- Encoding برابر UTF-8، بدون BOM و بدون Newline پایانی است.
- Serialization معادل `ensure_ascii=False`، `separators=(",", ":")` و `allow_nan=False` است؛ Payload به Objectهای وابسته به ترتیب Dictionary متکی نیست.
- نسخه‌ها در API عمومی ثابت‌اند: `jalali-date.v1`، `source-hash.v1` و `sheet-snapshot-hash.v1`.
- Digest برابر SHA-256 روی همان bytes و خروجی lowercase hexadecimal با طول 64 است.

Payload سطر این شکل دقیق را دارد:

```text
["source-hash.v1","raw-source-contract.v1",sheet_name,[
  [field_name,type_tag,canonical_value], ...
]]
```

فیلدها دقیقاً با ترتیب `raw_columns` رجیستری WP-02 نوشته می‌شوند. Mapping ورودی باید دقیقاً همه و فقط همین نام‌فیلدها را داشته باشد؛ کمبود، فیلد اضافه، شناسه فنی یا مقدار دارای نوع نامعتبر خطاست. قواعد مقدار عبارت‌اند از:

- `null`: برای هر نوع مجاز و از متن خالی متمایز است.
- `raw_text`: فقط `str` یا `None`؛ کدپوینت‌های Unicode، فاصله ابتدا/انتها و متن خالی عیناً حفظ می‌شوند و Trim، Unicode normalization یا جایگزینی حروف فارسی/عربی انجام نمی‌شود.
- `integer_toman`: مقدار صحیح پایه 10 به‌صورت رشته Canonical بدون `+` یا صفر پیشرو؛ `int` غیرBoolean، Decimal کاملاً صحیح یا متن عددی سخت‌گیرانه پذیرفته می‌شود.
- `decimal`: مقدار Decimal پایه 10 به‌صورت رشته Plain و بدون Exponent؛ صفر منفی به `0`، صفرهای غیرمعنادار انتهای اعشار حذف و دقت معنادار منبع حفظ می‌شود.
- در ورودی عدد/تاریخ، رقم‌های فارسی و عربی و فاصله بیرونی قابل نرمال‌سازی‌اند؛ جداکننده هزارگان، Exponent، Float، Boolean و مقدار غیرمتناهی رد می‌شوند.
- `date_raw` سه شیت تراکنشی با معنای `jalali_date` پردازش می‌شود: فقط متن با سال چهاررقمی، ماه/روز یک یا دو رقمی و جداکننده `/` یا `-` پذیرفته و به `YYYY-MM-DD` تبدیل می‌شود. هر ورودی منطبق با این Grammar صریحاً شمسی تفسیر می‌شود و تشخیص خودکار شمسی/میلادی وجود ندارد. اعتبار تقویمی و تبدیل میلادی با `persiantools` انجام می‌شود، نه الگوریتم دست‌نویس. نتیجه تغییرناپذیر شامل متن خام، تاریخ شمسی Canonical، تاریخ میلادی قابل Query، سال/ماه/روز شمسی، `fiscal_year` برابر سال شمسی و نسخه محاسبه است. مقدار مورد استفاده در Hash همان تاریخ شمسی Canonical است.

معادل‌های عددی یا تاریخی پذیرفته‌شده پس از Canonicalization Hash یکسان دارند؛ تغییر متن خام واقعی، Null/Text یا مقدار Canonical Hash را عوض می‌کند. حفظ مقدار خام در Storage مسئولیت Import/Revision بعدی است و این ADR آن را حذف نمی‌کند.

Payload Snapshot این شکل دقیق را دارد:

```text
["sheet-snapshot-hash.v1","raw-source-contract.v1",sheet_name,[
  [canonical_uuid7,source_hash], ...
]]
```

جفت‌ها بر اساس bytes شناسه UUIDv7 مرتب می‌شوند. UUID به نمایش lowercase hyphenated استاندارد تبدیل می‌شود. شناسه خالی، نامعتبر، غیر RFC، غیرنسخه 7 یا تکراری و Hash نامعتبر رد می‌شود. Snapshot خالی Hash قطعی دارد؛ جابه‌جایی/Sort جفت‌ها بی‌اثر و Insert/Delete/Edit مؤثر است.

`Asia/Tehran` با `zoneinfo` ثابت مرجع زمان Audit و نمایش است، ولی تابع تاریخ مالی ورودی زمانی دریافت نمی‌کند تا Save/Import نزدیک نیمه‌شب نتواند روز Excel را تغییر دهد. تبدیل Timestamp آگاه به منطقه زمانی ایران باید Naive datetime را رد کند.

## Consequences and trade-offs

- Agent و Server می‌توانند با Golden Vector مشترک سازگاری بایتی را ثابت کنند.
- متن‌های خام آگاهانه Normalize نمی‌شوند؛ بنابراین اصلاح فاصله یا حرف در نام/شرح یک Edit منبع است.
- نمایش‌های عددی/تاریخی معادل به یک مقدار Canonical می‌رسند و از Revision صرفاً ناشی از Representation جلوگیری می‌شود.
- `source_hash` معنای Ledger را تضمین نمی‌کند؛ تغییر Alias، Item Rule یا نسخه محاسبه بعداً باید `ledger_hash` را تغییر دهد، حتی اگر `source_hash` ثابت باشد.
- اندازه JSON فقط هنگام محاسبه سطر/Snapshot مصرف می‌شود و Payload یا Hash داخل Excel ذخیره نمی‌شود.

## Roadmap and acceptance impact

- مرجع: بخش‌های 4.2، 5.2، 5.5، 16 و O-03/O-25/O-26/O-42/O-68.
- WP-03 فقط `source_hash`، `sheet_snapshot_hash`، تاریخ Canonical و قواعد عدد منبع را می‌سازد و به‌تنهایی G1 را نمی‌بندد.
- `ledger_hash`، Revision/Void، Parser XLSX، Import اتمیک و Schema دیتابیس بسته‌های بعدی‌اند.

## Evidence plan

- Golden bytes و Digest ثابت برای هر چهار شیت و Snapshot خالی.
- Property test برای بی‌اثری ترتیب Mapping/جفت‌ها و اثر Insert/Delete/Edit.
- آزمون Null/Text، Unicode، Decimal/Integer و رد همه Float/Boolean/non-finite/فرمت‌های مبهم.
- آزمون نوروز، کبیسه/غیرکبیسه، پایان ماه، رفت‌وبرگشت، رقم فارسی/عربی، تاریخ نامعتبر و مرز `Asia/Tehran`.
- CI روی Windows و Linux با Python 3.13 و Lockfile Frozen.

## Migration and rollback impact

هنوز Hash عملیاتی یا داده Production تحت این قرارداد وجود ندارد؛ بنابراین نسخه 1 Migration داده‌ای ندارد. هر تغییر آینده که bytes یا معنای Canonical را عوض کند باید نسخه جدیدی مانند `source-hash.v2` ایجاد کند، Reader/Rebuild نسخه قبلی را نگه دارد و با Migration شاهددار فعال شود. Rollback WP-03 با حذف ماژول/وابستگی و بازگشت مصرف‌کنندگان ممکن است؛ Hashهای نسخه‌دار بعد از استفاده عملی نباید بازتفسیر شوند.

## Reconsideration triggers

- نیاز اثبات‌شده به پیاده‌سازی میان‌زبانی که JSON فعلی را ناسازگار نشان دهد؛
- مشاهده نوع واقعی Excel که Parser سخت‌گیرانه نسخه 1 نمی‌تواند بدون از دست‌دادن معنا نمایش دهد؛
- نیاز قانونی/عملی به تشخیص تغییر Representation عدد/تاریخ به‌عنوان Revision؛
- Benchmark که هزینه ساخت Snapshot کامل را نامتناسب نشان دهد.

## Approval and protected assets

این تصمیم فنی در محدوده چشم‌انداز مصوب، مطابق O-46/O-49 توسط Codex مدیر پروژه پذیرفته شده است. Antigravity حق تغییر ADR، Roadmap یا قرارداد نسخه 1 را ندارد و در صورت تناقض باید متوقف شود. Excel مرجع/کپی واقعی، SQLite یا داده واقعی، Secret، Production، DNS و شاخه `main` در WP-03 دست‌نخورده می‌مانند.
