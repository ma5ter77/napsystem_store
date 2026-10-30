"""Integer money arithmetic and the immutable double-entry journal."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import LedgerEntry, LedgerTransaction


def platform_share(net_minor: int, bps: int) -> int:
    """Platform share rounded half-up; the developer share is the remainder."""
    if net_minor < 0:
        raise ValueError("net must be >= 0")
    return (net_minor * bps + 5000) // 10000


def split(net_minor: int, bps: int) -> tuple[int, int]:
    p = platform_share(net_minor, bps)
    return p, net_minor - p


def post(db: Session, business_ref: str, kind: str, currency: str, lines: list[tuple[str, int]], memo: str = "") -> bool:
    """Insert a balanced transaction once per business_ref. Returns False if it already existed."""
    lines = [(a, v) for a, v in lines if v != 0]
    if sum(v for _, v in lines) != 0:
        raise ValueError(f"unbalanced transaction {business_ref}")
    if not lines:
        return False
    res = db.execute(insert(LedgerTransaction).values(business_ref=business_ref, kind=kind, currency=currency,
                                                      memo=memo)
                     .on_conflict_do_nothing(index_elements=["business_ref"]).returning(LedgerTransaction.id))
    tx_id = res.scalar()
    if tx_id is None:
        return False
    for account, amount in lines:
        db.add(LedgerEntry(transaction_id=tx_id, account=account, amount_minor=amount, currency=currency))
    db.flush()
    return True


def balance(db: Session, account: str, currency: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
        LedgerEntry.account == account, LedgerEntry.currency == currency)) or 0)


def balance_prefix(db: Session, prefix: str) -> dict[str, int]:
    rows = db.execute(select(LedgerEntry.currency, func.sum(LedgerEntry.amount_minor)).where(
        LedgerEntry.account.like(prefix + "%")).group_by(LedgerEntry.currency))
    return {c: int(s) for c, s in rows}


def dev_payable(dev_id) -> str:
    return f"developer:{dev_id}:payable"


def dev_in_transit(dev_id) -> str:
    return f"developer:{dev_id}:payout_in_transit"
