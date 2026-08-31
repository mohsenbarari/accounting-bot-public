# accounting-local-agent

Windows interactive user-session agent for monitoring Excel `.xlsx` saves, streaming literal source extraction, local SQLite mirroring and resilient outbox synchronization.

## Architecture and boundaries

- **Read-Only XLSX Source Reader (`xlsx_source_reader.py`):**
  - Public version: `XLSX_SOURCE_READER_VERSION = "xlsx-source-reader.v1"`
  - Public API: `read_xlsx_source_snapshot(path: Path | str) -> XlsxSourceReadResult`
  - Reuses authoritative contracts from `accounting_contracts` without duplicating registry or hashing rules.
  - Returns a validated `ValidatedSourceWorkbookSnapshot` (WP-04) alongside an immutable mapping of `UUIDv7 -> SourceRowLocation` (physical sheet and row number in `2..1,048,576`).
  - Locations do not enter source hashes, sheet snapshot hashes or change planning.

## Supported XLSX profile (ADR-0008)

1. **Package and relationships:**
   - Unencrypted standard `.xlsx` OPC ZIP packages in Transitional (`http://schemas.openxmlformats.org/spreadsheetml/2006/main`) and Strict (`http://purl.oclc.org/ooxml/spreadsheetml/main`) namespace families.
   - Dynamic package relationship traversal via `_rels/.rels` and `workbook.xml.rels`.
   - Exactly all four approved sheets (`خرید-فروش`, `دریافت-پرداخت`, `ورود-خروج`, `لیست کسبه`) must be declared and present; additional helper/report sheets are safely ignored without opening their XML.
2. **Formula and cache exclusion:**
   - Cells with `<f>` elements and cells within array/data-table formula `ref` ranges are excluded prior to value conversion; their values map to `None`.
   - Literal overrides in shared formula ranges are preserved.
   - Formula errors in excluded cells do not block ingestion.
3. **Literal decoding:**
   - `None` for missing cells, `""` for explicit empty text.
   - Numeric XML in Integer/Decimal columns converts directly to `Decimal` preserving finite stored lexemes.
   - Numeric XML in raw-text columns preserves numeric lexeme as `str`.
   - Financial dates (`date_raw`) and UUIDs require text; numeric XML or dates in these fields are rejected.
   - Booleans, errors, and invalid types in retained fields raise typed errors.
4. **Row activity and identity:**
   - Rows with literal non-blank values in `activity_columns` are active (numeric zero is active; empty/whitespace text is inactive).
   - Inactive rows (template, style-only, leftover date/ID) are omitted without creating identities or void events.
   - Active rows require a valid UUIDv7 in their designated technical ID column (`Z` for خرید-فروش, `P` for دریافت-پرداخت, `P` for ورود-خروج, `D` for لیست کسبه).

## Pre-commit and validation checks

```bash
uv lock --check
uv sync --frozen --all-packages --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest -v
git diff --check origin/main...HEAD
python3 .agents/skills/accounting-bot-implementer/scripts/validate_handoff.py handoffs/phase-01/wp-05-streaming-xlsx-source-reader/
```
