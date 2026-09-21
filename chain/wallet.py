"""Robinhood Chain connection + project wallet helpers (Sector 0 / Sector 1).

Connects to Robinhood Chain over public RPC with web3.py, verifies the chain ID
sanity check, and reads/prints the project wallet's native ETH balance. Sector 1
adds read-only balance/snapshot/transaction-history helpers returning the
normalized :mod:`chain.schema` shapes.

SECURITY: the private key is read ONLY from the environment / `.env`
(PRIVATE_KEY) and is never printed or logged. Sector 4's execution layer is the
only code allowed to sign/broadcast — nothing here signs anything.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent-core"))

from config import (  # noqa: E402
    PRIVATE_KEY,
    ROBINHOOD_CHAIN_ID_MAINNET,
    ROBINHOOD_CHAIN_ID_TESTNET,
    ROBINHOOD_RPC_MAINNET,
    ROBINHOOD_RPC_TESTNET,
)
from web3 import Web3  # noqa: E402

from chain import rpc as _rpc  # noqa: E402
from chain.schema import Balance, Snapshot, TransferItem  # noqa: E402

NETWORKS = {
    "testnet": (ROBINHOOD_RPC_TESTNET, ROBINHOOD_CHAIN_ID_TESTNET),
    "mainnet": (ROBINHOOD_RPC_MAINNET, ROBINHOOD_CHAIN_ID_MAINNET),
}
DEFAULT_NETWORK = "testnet"

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
]

_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def get_native_balance(w3: Web3, address: str) -> int:
    """Native token (ETH) balance of ``address`` in wei."""
    return w3.eth.get_balance(Web3.to_checksum_address(address))


def token_meta(w3: Web3, token_address: str) -> tuple:
    """Return ``(symbol, decimals)`` for an ERC-20 token address."""
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    symbol = token.functions.symbol().call()
    decimals = token.functions.decimals().call()
    return symbol, decimals


def get_token_balance(w3: Web3, address: str, token_address: str) -> Balance:
    """ERC-20 balance of ``address`` for one token (raw + human)."""
    addr = Web3.to_checksum_address(address)
    token = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    symbol, decimals = token.functions.symbol().call(), token.functions.decimals().call()
    raw = token.functions.balanceOf(addr).call()
    return Balance.build(asset=token_address, address=addr, symbol=symbol,
                         decimals=decimals, raw=raw)


def get_full_snapshot(w3: Web3, address: str, tokens=None, network: str = "testnet") -> Snapshot:
    """Native balance + balances for ``tokens`` (list of token addresses) in one snapshot."""
    addr = Web3.to_checksum_address(address)
    native = get_native_balance(w3, addr)
    token_list = list(tokens or [])
    balances = [
        Balance.build(asset="native", address=addr, symbol="ETH", decimals=18, raw=native)
    ]
    errors = []
    for taddr in token_list:
        try:
            balances.append(get_token_balance(w3, addr, taddr))
        except Exception as exc:  # noqa: BLE001
            errors.append({"token": taddr, "error": str(exc)[:200]})
    chain_id = w3.eth.chain_id if w3.eth.chain_id else NETWORKS.get(network, (None, 0))[1]
    return Snapshot(
        address=addr,
        network=network,
        chain_id=int(chain_id),
        block_number=int(w3.eth.block_number),
        queried_at=datetime.now(timezone.utc).isoformat(),
        balances=balances,
        errors=errors,
    )


def _topic_address(address: str) -> str:
    """32-byte RPC topic value for an indexed address (left-padded)."""
    clean = Web3.to_checksum_address(address)[2:].lower()
    return "0x" + "0" * 24 + clean


def get_transaction_history(w3: Web3, address: str, tokens=None, from_block=None,
                            lookback_blocks: int = 600_000) -> list:
    """ERC-20 transfer history for ``address``, normalized to TransferItem.

    Uses RPC-native eth_getLogs (Transfer events); no block-explorer API is
    assumed. Native ETH transfers have no logs and are intentionally skipped.
    """
    w3rpc = getattr(w3.provider, "endpoint_uri", None)
    if not w3rpc:
        raise RuntimeError("provider has no endpoint_uri")

    latest = int(w3.eth.block_number)
    start = from_block if from_block is not None else max(0, latest - lookback_blocks)
    addr = Web3.to_checksum_address(address)
    from_topic, to_topic = _topic_address(addr), _topic_address(addr)

    entries = []
    for taddr in tokens or []:
        contract = Web3.to_checksum_address(taddr)
        try:
            symbol, decimals = token_meta(w3, contract)
        except Exception as exc:  # noqa: BLE001
            entries.append(_ErrToken(taddr, f"symbol/decimals failed: {exc}"))
            continue
        try:
            logs_out = _rpc.eth_get_logs(
                w3rpc, contract, [None, [from_topic]], from_block=start, to_block=latest)
            logs_in = _rpc.eth_get_logs(
                w3rpc, contract, [None, None, [to_topic]], from_block=start, to_block=latest)
        except Exception as exc:  # noqa: BLE001
            entries.append(_ErrToken(taddr, f"log query failed: {exc}"))
            continue
        for log in logs_out:
            entries.append(_transfer(log, contract, symbol, decimals, addr, "out"))
        for log in logs_in:
            entries.append(_transfer(log, contract, symbol, decimals, addr, "in"))

    entries = [e for e in entries if isinstance(e, TransferItem)]
    entries.sort(key=lambda t: t.block_number)
    return entries


class _ErrToken:
    def __init__(self, token, error):
        self.token, self.error = token, error


def _strip_hex(value) -> str:
    if isinstance(value, str):
        return value[2:] if value[:2].lower() == "0x" else value
    return value.hex()


def _transfer(log, contract, symbol, decimals, wallet, direction) -> TransferItem:
    topics = log["topics"]
    from_addr = "0x" + _strip_hex(topics[1])[-40:]
    to_addr = "0x" + _strip_hex(topics[2])[-40:]
    raw = int(log["data"], 16)
    raw_readable = raw / (10 ** decimals)
    rounded = float(f"{raw_readable:.6f}")
    return TransferItem(
        token=contract, symbol=symbol, from_addr=from_addr, to_addr=to_addr,
        amount_raw=str(raw), amount_human=rounded, tx_hash=log["transactionHash"],
        block_number=int(log["blockNumber"], 16), direction=direction,
    )


def connect(rpc: str) -> Web3:
    """Return a connected web3 session; raise if the RPC is unreachable."""
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC: {rpc}")
    return w3


def project_account(w3: Web3):
    """Derive the project wallet (address only) from PRIVATE_KEY."""
    if not PRIVATE_KEY:
        raise RuntimeError(
            "PRIVATE_KEY is not set. Put your project wallet key in .env "
            "(see README -> 'Create a project wallet')."
        )
    return w3.eth.account.from_key(PRIVATE_KEY)


def describe(w3: Web3, network: str) -> None:
    """Chain ID sanity check + wallet address + native ETH balance."""
    chain_id = w3.eth.chain_id
    expected = NETWORKS[network][1]
    status = "OK" if chain_id == expected else "MISMATCH"
    print(f"[chain] network   = {network}   (expected chain id {expected})")
    print(f"[chain] chain id  = {chain_id}  -> {status}")
    if chain_id != expected:
        raise SystemExit(
            f"FATAL: RPC returned chain id {chain_id}, expected {expected}. "
            "Is the RPC pointing at the right network?"
        )

    acct = project_account(w3)
    balance = w3.eth.get_balance(acct.address)
    print(f"[chain] wallet    = {acct.address}")
    print(f"[chain] ETH       = {w3.from_wei(balance, 'ether')}")
    print(f"[chain] latest    = block {w3.eth.block_number}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Robinhood Chain wallet connectivity check (Sector 0)."
    )
    parser.add_argument(
        "--network",
        choices=sorted(NETWORKS),
        default=DEFAULT_NETWORK,
        help=f"network to check (default: {DEFAULT_NETWORK})",
    )
    args = parser.parse_args()

    rpc, _expected = NETWORKS[args.network]
    print(f"[chain] connecting to {rpc}")
    try:
        w3 = connect(rpc)
    except Exception as exc:  # noqa: BLE001
        print(f"[chain] FAILED to connect: {exc}", file=sys.stderr)
        return 1
    describe(w3, args.network)
    return 0


if __name__ == "__main__":
    sys.exit(main())