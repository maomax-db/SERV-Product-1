"""Minimal, dependency-free JSON-RPC helpers for Robinhood Chain.

web3.py's ``eth.get_logs`` has quirks against this node (topics must be tuples
and arbitrary OR filters are not portable), so market/wallet reads that need raw
logs or storage access go through this thin RPC client instead. Request bodies
are plain ``urllib`` POSTs with a web3-style User-Agent, which this node accepts
(403 without it) and which matched every probe in this repo.
"""

import json
import urllib.request

USER_AGENT = "python-web3/8.0.0"


def json_rpc(rpc_url: str, method: str, params, timeout: int = 60):
    """POST a single JSON-RPC request and return the decoded ``result``.

    Raises :class:`RuntimeError` with the RPC error object on failure.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload["result"]


def _hex_block(value) -> str:
    if value == "latest":
        return "latest"
    if isinstance(value, int):
        return hex(value)
    return value


def eth_get_logs(rpc_url: str, address: str, topics=None, from_block="latest",
                 to_block="latest", timeout: int = 60) -> list:
    """eth_getLogs over a single contract, tolerant of single-value topics.

    ``topics`` is a list of topic filters for each indexed position; each entry
    is either None/[] (any) or a list of OR'd topic hex values.
    """
    filt = {
        "fromBlock": _hex_block(from_block),
        "toBlock": _hex_block(to_block),
        "address": address,
    }
    if topics:
        normalized = [[t] if isinstance(t, str) else (t or []) for t in topics]
        filt["topics"] = normalized
    return json_rpc(rpc_url, "eth_getLogs", [filt], timeout=timeout)


def eth_call(rpc_url: str, to: str, data: str, block="latest", timeout: int = 60) -> str:
    """eth_call returning the raw 0x-encoded return data."""
    return json_rpc(rpc_url, "eth_call", [{"to": to, "data": data}, block], timeout=timeout)


def eth_get_code(rpc_url: str, address: str, timeout: int = 60) -> str:
    return json_rpc(rpc_url, "eth_getCode", [address, "latest"], timeout=timeout)