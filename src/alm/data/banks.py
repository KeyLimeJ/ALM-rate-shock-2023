"""Registry of banks studied in this project, keyed by FFIEC RSSD ID.

RSSD (Replication Server System Database) ID is the Federal Reserve's primary
key for a regulated institution. It is what FFIEC Call Reports are keyed on and
what the FDIC's "Institution Directory" and "BankFind" tools use.

Why these two banks: see survivor-bank rationale in the project README. SVB is
the casualty; Huntington is chosen as the survivor contrast for similar asset
size, opposite-end deposit-franchise stickiness, and opposite-end HTM duration
discipline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bank:
    rssd_id: int
    name: str
    short_name: str
    role: str  # "casualty" or "survivor"
    legal_entity: str
    failure_date: str | None = None  # ISO date, if applicable


BANKS: dict[str, Bank] = {
    "svb": Bank(
        rssd_id=802866,
        name="Silicon Valley Bank",
        short_name="SVB",
        role="casualty",
        legal_entity="Silicon Valley Bank, a California state-chartered commercial bank",
        failure_date="2023-03-10",
    ),
    # ASSUMPTION: Huntington National Bank (the chartered bank subsidiary of
    # Huntington Bancshares, HBAN) — RSSD 12311. The HBAN holding company files
    # FR Y-9C; the bank itself files the Call Report we're using.
    "hban": Bank(
        rssd_id=12311,
        name="The Huntington National Bank",
        short_name="Huntington",
        role="survivor",
        legal_entity="The Huntington National Bank, OCC-chartered national bank",
    ),
}


def get(key: str) -> Bank:
    key = key.lower()
    if key not in BANKS:
        raise KeyError(f"Unknown bank key {key!r}. Available: {sorted(BANKS)}")
    return BANKS[key]


def all_rssd_ids() -> list[int]:
    return [b.rssd_id for b in BANKS.values()]
