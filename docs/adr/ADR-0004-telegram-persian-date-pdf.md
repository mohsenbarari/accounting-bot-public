# ADR-0004: Telegram، تاریخ شمسی و PDF فارسی

- Status: Accepted
- Date: 2026-08-28
- Work Package: Phase 0 — Architecture baseline
- Decision owner: Product Owner؛ ثبت و بازبینی توسط Codex

## Context

گزارش رسمی روزانه و اصلاحیه متن Telegram هستند و PDF فقط برای گزارش درخواستی تولید می‌شود. رابط باید تقویم شمسی، `Asia/Tehran`، متن RTL و PDF شبیه ساختار «ریزحساب» را پشتیبانی کند. این ADR تصمیم O-55 را ثبت می‌کند.

## Constraints

- Telegram فقط رابط تحویل است و قواعد مالی در Domain قرار دارند.
- گزارش روزانه/اصلاحیه PDF نیست؛ گزارش درخواستی فقط PDF است.
- تاریخ خام Excel حفظ و تبدیل شمسی نسخه‌ثابت و آزموده است.
- PDF باید RTL/A4، قابل استخراج و دارای فونت محلی باشد.

## Options considered

### Option A — aiogram، persiantools/zoneinfo و Chromium

- Benefits: Bot async، Webhook مناسب، رندر بالغ HTML/CSS و متن دوجهته، Template قابل آزمون.
- Costs/risks: Chromium مصرف RAM و Dependency بزرگ‌تری دارد و باید در Worker جدا اجرا شود.
- Reversibility: بالا در مرز Adapter/Renderer.

### Option B — کتابخانه Telegram دیگر و WeasyPrint

- Benefits: زنجیره PDF سبک‌تر در بعضی محیط‌ها.
- Costs/risks: معیار RTL/Bidi فارسی پروژه را بدون شاهد کافی تضمین نمی‌کند؛ تغییر Bot SDK نیز مزیت محصولی مشخصی ندارد.
- Reversibility: متوسط.

### Option C — تبدیل تاریخ دست‌نویس و PDF مستقیم با Canvas

- Benefits: کنترل سطح پایین.
- Costs/risks: ریسک بالای تقویم، RTL، صفحه‌بندی و نگهداری؛ آزمون‌پذیری کمتر.
- Reversibility: پایین.

## Recommendation

Option A انتخاب شده است: `aiogram 3` روی Webhook با Secret Token، `persiantools` همراه `zoneinfo` برای شمسی و `Asia/Tehran`، `Jinja2` برای Template و `Playwright + Chromium` با فونت محلی Vazirmatn برای PDF RTL/A4. PDF در Worker جدا تولید می‌شود.

## Roadmap and acceptance impact

- مرجع: O-55، بخش‌های 8، 14، 16 و معیارهای 19.2 تا 19.4.
- Lockfile نسخه کتابخانه تاریخ و Browser را ثابت می‌کند.
- PDF شماره موبایل، Telegram ID یا داده فنی اضافه ندارد و فایل موقت پس از تحویل موفق پاک می‌شود.
- Callback و دسترسی برای هر درخواست در Backend دوباره کنترل می‌شوند.

## Evidence plan

- G2/G5: Golden Test نوروز، کبیسه، پایان ماه، ورودی نامعتبر، رفت‌وبرگشت و `Asia/Tehran`.
- G3: Golden Text برای گزارش روزانه/اصلاحیه و استخراج متن PDF.
- G3/G6: Visual Regression سند «ریزحساب» RTL/A4، فونت، Page break و بازه بزرگ.
- G6: Webhook Secret، Callback منقضی، مجوز نقش، Rate limit و Retry Telegram.
- G6: Prototype تقویم شمسی Inline و ورودی تایپی ارقام فارسی/عربی/لاتین.

## Migration and rollback impact

Templateها و Renderer پشت قرارداد Reporting قرار دارند و می‌توان Renderer را بدون تغییر Domain جایگزین کرد. تغییر محتوای گزارش یا قاعده تاریخ، تصمیم محصولی جداگانه است و با تعویض کتابخانه فنی مجاز نمی‌شود.

## Reconsideration triggers

- Chromium در آزمون بار یا محیط استقرار بودجه منابع را نقض کند؛
- Visual/Extraction test نقص غیرقابل‌حل RTL نشان دهد؛
- Telegram API یا کتابخانه انتخابی پشتیبانی لازم را متوقف کند.

## Approval required

این تصمیم در O-55 تأیید شده است. جایگزینی فنی فقط با شاهد، ADR و بازبینی Codex ممکن است؛ تغییر قابل مشاهده گزارش نیازمند تأیید کاربر است و اجرا پس از G0 آغاز می‌شود.
