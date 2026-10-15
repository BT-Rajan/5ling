"""Tax on invoices (BRD FR-FE-09).

Rates are rows in `tax_rates`, each from a date; the one in force on the invoice date applies,
and the invoice keeps the rate it used. Amounts are whole paise. Each tax line is rounded half
up to the paise on its own: CGST and SGST are each half the rate on the amount; IGST is the full
rate. IGST applies only to a client registered for GST in a different state from the firm;
otherwise (same state, or an unregistered client, taxed where the firm is) CGST and SGST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TaxRate

GST = "GST"


def half_up(numerator: int, denominator: int) -> int:
    """numerator / denominator, rounded half up, for non-negative whole numbers."""
    if numerator < 0 or denominator <= 0:
        raise ValueError("half_up takes a non-negative numerator and a positive denominator")
    return (2 * numerator + denominator) // (2 * denominator)


@dataclass(frozen=True)
class Tax:
    cgst: int
    sgst: int
    igst: int

    @property
    def total(self) -> int:
        return self.cgst + self.sgst + self.igst


def tax_for(amount_paise: int, rate_bp: int, *, inter_state: bool) -> Tax:
    if inter_state:
        return Tax(0, 0, half_up(amount_paise * rate_bp, 10_000))
    half = half_up(amount_paise * rate_bp, 20_000)
    return Tax(half, half, 0)


def is_inter_state(firm_state: str | None, client_gstin: str | None) -> bool:
    if firm_state is None or not client_gstin:
        return False
    return client_gstin[:2] != firm_state


def rate_on(db: Session, day: date, code: str = GST) -> TaxRate | None:
    return db.scalar(
        select(TaxRate)
        .where(TaxRate.code == code, TaxRate.effective_from <= day)
        .order_by(TaxRate.effective_from.desc())
        .limit(1)
    )
