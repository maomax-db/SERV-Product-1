"""Sector 1 — normalized internal data schema.

Everything the read-only wallet/market layer returns is a plain dataclass that
serializes to JSON via :meth:`to_dict`. This is the single shared shape feeding
Sector 2 (SERV proposal) and Sector 3 (guardrail decisions); nothing else in the
codebase may define its own ad-hoc shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


def _human(raw: int, decimals: int) -> str:
    """Fixed-point formatting aware of token decimals (raw int -> scaled str)."""
    neg = raw < 0
    scaled = abs(raw) / (10 ** decimals)
    text = f"{scaled:.6f}".rstrip("0").rstrip(".")
    return f"-{text}" if neg else text


@dataclass
class Balance:
    asset: str
    address: str
    symbol: str
    decimals: int
    raw: str
    human: float

    @classmethod
    def build(cls, asset: str, address: str, symbol: str, decimals: int, raw: int) -> "Balance":
        return cls(asset=asset, address=address, symbol=symbol, decimals=decimals,
                   raw=str(raw), human=float(_human(raw, decimals)))

    def to_dict(self):
        d = asdict(self)
        d["human"] = float(self.human)
        return d


@dataclass
class Snapshot:
    address: str
    network: str
    chain_id: int
    block_number: int
    queried_at: str
    balances: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class TransferItem:
    token: str
    symbol: str
    from_addr: str
    to_addr: str
    amount_raw: str
    amount_human: float
    tx_hash: str
    block_number: int
    direction: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Quote:
    ok: bool
    network: str
    chain_id: int
    block_number: int
    pool_id: Optional[str] = None
    exchange: str = "Uniswap v4 (Robinhood Chain PoolManager, direct state read)"
    token_in: Optional[str] = None
    token_out: Optional[str] = None
    token_in_symbol: Optional[str] = None
    token_out_symbol: Optional[str] = None
    amount_in_raw: Optional[str] = None
    amount_in_human: Optional[str] = None
    amount_out_raw: Optional[str] = None
    amount_out_human: Optional[str] = None
    price1_per_0: Optional[float] = None
    price0_per_1: Optional[float] = None
    fee_basis: Optional[float] = None
    sqrt_price_x96: Optional[int] = None
    liquidity: Optional[int] = None
    tick: Optional[int] = None
    reserve_hint: Optional[str] = None
    error_type: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def error_quote(cls, network: str, chain_id: int, block_number: int, error: str,
                    error_type: str = "error") -> "Quote":
        return cls(ok=False, network=network, chain_id=chain_id, block_number=block_number,
                   error=error, error_type=error_type)

    def to_dict(self):
        return asdict(self)