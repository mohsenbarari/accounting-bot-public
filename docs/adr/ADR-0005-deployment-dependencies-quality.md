# ADR-0005: استقرار تک‌سرور، وابستگی‌ها و کنترل کیفیت

- Status: Accepted
- Date: 2026-08-28
- Work Package: Phase 0 — Architecture baseline
- Decision owner: مالک محصول برای تصمیم اصلی؛ حاکمیت اجرای فعلی با Codex مدیر پروژه طبق Roadmap 0.31

## Context

نسخه اول روی یک سرور هتزنر اجرا می‌شود و به TLS عمومی، استقرار قابل‌بازیابی، وابستگی تکرارپذیر و آزمون قوی قواعد مالی/Idempotency نیاز دارد، بدون آنکه Kubernetes یا سامانه‌های خارجی غیرضروری اضافه شوند. این ADR تصمیم O-56 را ثبت می‌کند.

## Constraints

- سرور مرکزی تک‌میزبان است؛ Agent ویندوز Container نمی‌شود.
- دامنه نهایی هنوز انتخاب نشده و ورودی مؤجل فاز 4 است.
- Secret، داده واقعی، Excel و SQLite عملیاتی نباید وارد Git یا Image شوند.
- مخزن عملیاتی Public است؛ فقط کد و مستندات عمومی‌پذیر و Fixture ساختگی Commit می‌شوند و هر داده واقعی، Secret و پیکربندی واقعی استقرار خارج از Git می‌ماند.

## Options considered

### Option A — Docker Compose، Caddy و Toolchain Python قفل‌شده

- Benefits: استقرار و Rollback قابل فهم، ACME خودکار، محیط یکسان و هزینه عملیاتی متناسب با یک سرور.
- Costs/risks: یک Failure domain مرکزی و نیاز به Backup/Restore آزموده؛ Compose مقیاس‌دهی ارکستریت‌شده ندارد.
- Reversibility: بالا در محدوده تک‌سرور.

### Option B — نصب Bare-metal با systemd

- Benefits: لایه کمتر و مصرف جزئی پایین‌تر.
- Costs/risks: Drift وابستگی، Reproduction و Rollback دشوارتر؛ Browser/PostgreSQL/Caddy روی میزبان درهم می‌شوند.
- Reversibility: متوسط.

### Option C — Kubernetes و Service Mesh

- Benefits: Orchestration و مقیاس چندگرهی.
- Costs/risks: پیچیدگی و هزینه نامتناسب، سطح خطا و نگهداری بیشتر.
- Reversibility: پایین تا متوسط.

## Recommendation

Option A انتخاب شده است. سرور با Docker Compose شامل Caddy، API/Webhook، Worker و PostgreSQL اجرا می‌شود. Caddy TLS عمومی و ACME را مدیریت می‌کند. وابستگی‌ها با `uv` و `uv.lock` قفل می‌شوند.

کنترل کیفیت شامل `pytest`، `pytest-asyncio`، `Hypothesis`، Testcontainers، `Ruff`، `mypy` و Structured JSON Logging است. CI در صورت Lockfile ناسازگار، آزمون یا کنترل ایستا شکست می‌خورد. Kubernetes، Service Mesh، Redis، Celery، RabbitMQ و مانیتورینگ خارجی پولی بدون شاهد و ADR تازه خارج از نسخه اول‌اند.

## Roadmap and acceptance impact

- مرجع: O-56، بخش‌های 15.5/15.6، 18 و 19.5/19.6.
- Compose باید Healthcheck، Restart policy و Migration کنترل‌شده داشته باشد.
- فقط پورت 443 عمومی است و Caddy درخواست را به API/Webhook داخلی می‌رساند.
- Backup، PITR، Restore و Rollback پیش از Production شاهد می‌خواهند.

## Evidence plan

- G0/G1: نصب تکرارپذیر `uv.lock` روی Windows و Linux هدف و CI اولیه.
- G4: Compose آزمایشی، Healthcheck، Restart میزبان، Migration و Caddy روی دامنه آزمایشی.
- G4/G7: ACME renewal rehearsal، پورت‌های بسته و Secret injection خارج از Repo/Image.
- G7: PITR با RPO 15 دقیقه، Backup جدا، Restore با RTO حداکثر 4 ساعت و Rollback نسخه برنامه.
- در هر PR عمومی: Secret/Data Scan و اثبات نبود داده واقعی یا پیکربندی واقعی استقرار.

## Migration and rollback impact

Imageها و Compose versioned هستند و نسخه برنامه قابل Rollback است. Migration دیتابیس پیش‌فرض Additive و Backward-compatible است؛ هر Migration مخرب به Backup، Rehearsal، Rollback plan و اختیار هدف‌دار مالک نیاز دارد. Rollback نباید Job یا تاریخچه مالی/ممیزی را حذف کند.

## Reconsideration triggers

- نیاز واقعی چندمیزبانی یا SLA که Compose تک‌سرور را ناکافی کند؛
- Benchmark نیاز به Broker یا Scale مستقل را اثبات کند؛
- محدودیت ارائه‌دهنده یا Compliance مدل استقرار را تغییر دهد.

## Approval required

این تصمیم در O-56 تأیید شده و G0 در 2026-08-30 بسته شده است. Branch، Commit، Push، PR و Merge مخزن عملیاتی طبق Work Package و تصمیم Codex مدیر پروژه انجام می‌شوند؛ DNS، گواهی، استقرار/تغییر Production، هزینه تازه یا داده واقعی اختیار هدف‌دار مالک را می‌خواهند.
