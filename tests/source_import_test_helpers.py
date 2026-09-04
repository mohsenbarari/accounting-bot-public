"""Synthetic test helpers, models, and generators for source_import_store tests."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from accounting_contracts.raw_input_contracts import (
    BUSINESS_PARTIES_CONTRACT,
    BUY_SELL_CONTRACT,
    INVENTORY_MOVEMENTS_CONTRACT,
    RECEIPTS_PAYMENTS_CONTRACT,
)
from accounting_contracts.source_change_plan import (
    SourceRowInput,
    SourceSheetInput,
    ValidatedSourceWorkbookSnapshot,
    build_source_workbook_snapshot,
)
from accounting_persistence.source_import_store import (
    SOURCE_CHANGE_EVENT_VERSION,
)


def make_deterministic_uuid7(seed: int, timestamp_ms: int = 1725450000000) -> uuid.UUID:
    """Generate a valid, deterministic RFC 4122 UUIDv7 for tests."""
    time_ms = timestamp_ms + (seed & 0xFFFFF)
    f1 = (time_ms >> 16) & 0xFFFFFFFF
    f2 = time_ms & 0xFFFF
    f3 = 0x7000 | ((seed >> 16) & 0x0FFF)
    f4 = 0x80 | ((seed >> 8) & 0x3F)
    f5 = seed & 0xFF
    f6 = (0x010203040500 + seed) & 0xFFFFFFFFFFFF
    return uuid.UUID(fields=(f1, f2, f3, f4, f5, f6))


def build_synthetic_snapshot(
    parties: list[tuple[uuid.UUID, dict[str, Any]]],
    buy_sell: list[tuple[uuid.UUID, dict[str, Any]]],
    receipts_payments: list[tuple[uuid.UUID, dict[str, Any]]],
    inventory: list[tuple[uuid.UUID, dict[str, Any]]],
) -> ValidatedSourceWorkbookSnapshot:
    """Build an immutable four-sheet ValidatedSourceWorkbookSnapshot from row specs."""
    p_inputs = [SourceRowInput(u, v) for u, v in parties]
    bs_inputs = [SourceRowInput(u, v) for u, v in buy_sell]
    rp_inputs = [SourceRowInput(u, v) for u, v in receipts_payments]
    inv_inputs = [SourceRowInput(u, v) for u, v in inventory]

    sheets = [
        SourceSheetInput(BUSINESS_PARTIES_CONTRACT.sheet_name, p_inputs),
        SourceSheetInput(BUY_SELL_CONTRACT.sheet_name, bs_inputs),
        SourceSheetInput(RECEIPTS_PAYMENTS_CONTRACT.sheet_name, rp_inputs),
        SourceSheetInput(INVENTORY_MOVEMENTS_CONTRACT.sheet_name, inv_inputs),
    ]
    return build_source_workbook_snapshot(sheets)


def make_sample_party_row(
    name: str = "شرکت آلفا", phone: str | None = "09121234567"
) -> dict[str, Any]:
    return {"party_name_raw": name, "phone_number_raw": phone}


def make_sample_buy_sell_row(
    date_raw: str = "1403/01/15",
    party: str = "شرکت آلفا",
    t_type: str = "فروش",
    item: str = "کالای الف",
    qty: str = "2",
    unit_price: str = "1000",
    discount: str = "0",
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        "date_raw": date_raw,
        "party_name_raw": party,
        "transaction_type_raw": t_type,
        "item_name_raw": item,
        "quantity_raw": qty,
        "unit_price_toman_raw": unit_price,
        "discount_toman_raw": discount,
        "notes_raw": notes,
    }


def make_sample_receipt_payment_row(
    date_raw: str = "1403/01/16",
    party: str = "شرکت آلفا",
    entry_type: str = "دریافت",
    amount: str = "500",
    notes: str | None = None,
    account_code: str = "101",
    flag: str = "بله",
) -> dict[str, Any]:
    return {
        "date_raw": date_raw,
        "party_name_raw": party,
        "entry_type_raw": entry_type,
        "amount_toman_raw": amount,
        "notes_raw": notes,
        "account_code_raw": account_code,
        "customer_flag_raw": flag,
    }


def make_sample_inventory_row(
    date_raw: str = "1403/01/17",
    party: str = "شرکت آلفا",
    movement_type: str = "ورود",
    item: str = "کالای الف",
    qty: str = "10",
    purity: str = "750",
    notes: str | None = None,
    flag: str = "بله",
) -> dict[str, Any]:
    return {
        "date_raw": date_raw,
        "party_name_raw": party,
        "movement_type_raw": movement_type,
        "item_name_raw": item,
        "quantity_raw": qty,
        "purity_raw": purity,
        "notes_raw": notes,
        "customer_flag_raw": flag,
    }


def independent_compute_event_payload(
    *,
    device_id: uuid.UUID,
    event_id: uuid.UUID,
    import_id: uuid.UUID,
    sequence: int,
    source_id: uuid.UUID,
    fiscal_year: int,
    sheet_name: str,
    stable_id: uuid.UUID,
    revision: int,
    operation: str,
    financial_date: str | None,
    source_hash: str | None,
    raw_payload_base64: str | None,
    previous_version_hash: str | None,
    observed_at_utc: datetime,
) -> tuple[bytes, str]:
    """Independently construct the exact UTF-8 event bytes and SHA-256 hash."""
    event_array = [
        SOURCE_CHANGE_EVENT_VERSION,
        str(device_id).lower(),
        str(event_id).lower(),
        str(import_id).lower(),
        str(sequence),
        str(source_id).lower(),
        str(fiscal_year),
        sheet_name,
        str(stable_id).lower(),
        str(revision),
        operation,
        financial_date,
        source_hash,
        raw_payload_base64,
        previous_version_hash,
        observed_at_utc.isoformat(),
    ]
    raw_bytes = json.dumps(
        event_array, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    payload_hash = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, payload_hash
