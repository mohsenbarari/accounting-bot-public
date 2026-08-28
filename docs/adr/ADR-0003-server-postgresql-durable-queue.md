# ADR-0003: FastAPI، PostgreSQL و صف پایدار مرکزی

- Status: Accepted
- Date: 2026-08-28
- Work Package: Phase 0 — Architecture baseline
- Decision owner: Product Owner؛ ثبت و بازبینی توسط Codex

## Context

سرور باید Sync امضاشده، دفتر مالی نسخه‌دار، گزارش، Retry و رقابت Workerها را با تراکنش و Idempotency یکپارچه نگه دارد. حجم نسخه اول نیازمند Broker جدا نیست. این ADR تصمیم O-54 و قواعد Checkpoint مصوب مرحله 7 Import را ثبت می‌کند.

## Constraints

- SQLite فقط Mirror/Outbox محلی است و منبع مرکزی PostgreSQL خواهد بود.
- Jobها باید در Crash، Restart و قطعی از بین نروند و فقط در RAM نباشند.
- اصلاحیه پیش از Commit کامل Checkpoint ساخته نمی‌شود.
- Redis، Celery و RabbitMQ در نسخه اول بدون شاهد و ADR تازه مجاز نیستند.

## Options considered

### Option A — FastAPI و صف جدولی PostgreSQL

- Benefits: تراکنش اتمیک داده و Job، قید یکتا و Idempotency، Backup/PITR مشترک و عملیات ساده روی یک سرور.
- Costs/risks: نیازمند طراحی دقیق Lease، Index و Cleanup؛ بار بسیار بالا ممکن است بعداً Broker بخواهد.
- Reversibility: بالا با حفظ قرارداد Job و Outbox.

### Option B — PostgreSQL همراه Redis/Celery یا RabbitMQ

- Benefits: اکوسیستم آماده Worker و Routing.
- Costs/risks: سرویس، Backup، Consistency و Failure mode بیشتر؛ خطر Dual Write میان DB و Broker.
- Reversibility: متوسط.

### Option C — SQLite به‌عنوان دیتابیس مرکزی

- Benefits: استقرار ساده.
- Costs/risks: Writerهای هم‌زمان، Queue claim، دسترسی شبکه و رشد عملیاتی نامناسب.
- Reversibility: پایین پس از ورود داده واقعی.

## Recommendation

Option A انتخاب شده است: `FastAPI`، `Pydantic v2`، `SQLAlchemy 2.0`، `Alembic`، `psycopg 3` و PostgreSQL 18.

صف مرکزی جدول‌های PostgreSQL با `due_at`، Attempt، Lease، Idempotency Key و Claim اتمیک با `FOR UPDATE SKIP LOCKED` دارد. High-watermark امضاشده فقط پس از Commit پیوسته Sequenceها یک Checkpoint را می‌بندد. Job گزارش با کلید «شخص + تاریخ مالی + نوع گزارش + نسخه Checkpoint» یکتا است؛ ACK میانی، Retry و Crash Job تازه نمی‌سازند.

## Roadmap and acceptance impact

- مرجع: O-54، بخش‌های 5.2، 15.3، 19.5 و قواعد Retry/Backlog.
- Migrationها باید نسخه‌دار، ترجیحاً Additive و قابل Rollback باشند.
- تاریخ منتشرنشده اصلاحیه نمی‌سازد و ترتیب Sync، اصلاحیه، Catch-up و PDF باید پایدار بماند.

## Evidence plan

- G2/G4: Testcontainers با PostgreSQL 18 و Migration از دیتابیس خالی.
- G4: دو Worker هم‌زمان، Claim یکتا، Crash پس از Claim، انقضای Lease و Retry Idempotent.
- G4: Batch ناقص، Sequence gap، ACK گم‌شده و Checkpoint زودهنگام بدون ساخت Job.
- G4: Backlog مصنوعی 90روزه و سناریوی 1,000 تغییر روزانه.
- G7: Backup، PITR با RPO حداکثر 15 دقیقه و Restore آزموده.

## Migration and rollback impact

Rollback برنامه باید با Schema فعلی سازگار باشد. Migration مخرب، حذف تاریخچه مالی/ممیزی یا Queue و تغییر Backend دیتابیس نیازمند Backup، Rehearsal، ADR و تأیید جداگانه کاربر است.

## Reconsideration triggers

- Benchmark نشان دهد PostgreSQL Queue SLA یا توان عملی لازم را تأمین نمی‌کند؛
- استقرار چندسروری یا Routing پیچیده به نیاز واقعی تبدیل شود؛
- عملیات Queue بر سلامت OLTP اثر اثبات‌شده بگذارد.

## Approval required

این تصمیم در O-54 و مراحل Import تأیید شده است. افزودن Broker یا تغییر دیتابیس تصمیم تازه و منوط به ADR و تأیید کاربر است؛ اجرای فعلی پس از G0 آغاز می‌شود.
