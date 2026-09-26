"""Environment configuration for Weryon (Product #1 — Guardrail & Audit Layer).

Loads settings from the process environment and, failing that, from a local
`.env` file at the repo root (no third-party dependency required). Real values
belong in `.env` (git-ignored); see `.env.example` for the shape.

Note: the `agent-core` folder name (hyphen) is not a valid Python module path,
so sibling modules import each other flatly (see ``test_openserv.py``).
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal `.env` loader: `KEY=VALUE` per line, `#` comments, blank lines skipped.

    Existing environment variables win over the file, mirroring standard dotenv
    behaviour, so you can also export these without a `.env` file.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(REPO_ROOT / ".env")

# OpenServ / SERV Reasoning
OPENSERV_API_KEY = os.environ.get("OPENSERV_API_KEY", "")
OPENSERV_BASE_URL = os.environ.get(
    "OPENSERV_BASE_URL", "https://inference-api.openserv.ai/v1"
).rstrip("/")
OPENSERV_MODEL = os.environ.get("OPENSERV_MODEL", "gpt-5.4-mini")

# Robinhood Chain networks. Testnet is the primary dev target; mainnet exists
# for the final small-amount live demo (see README -> "Testnet liquidity").
ROBINHOOD_RPC_TESTNET = os.environ.get(
    "ROBINHOOD_RPC_TESTNET", "https://rpc.testnet.chain.robinhood.com"
).rstrip("/")
ROBINHOOD_CHAIN_ID_TESTNET = int(
    os.environ.get("ROBINHOOD_CHAIN_ID_TESTNET", "46630")
)
ROBINHOOD_RPC_MAINNET = os.environ.get(
    "ROBINHOOD_RPC_MAINNET", "https://rpc.mainnet.chain.robinhood.com"
).rstrip("/")
ROBINHOOD_CHAIN_ID_MAINNET = int(os.environ.get("ROBINHOOD_CHAIN_ID_MAINNET", "4663"))
ROBINHOOD_TESTNET_EXPLORER = "https://explorer.testnet.chain.robinhood.com"
ROBINHOOD_MAINNET_EXPLORER = "https://robinhoodchain.blockscout.com"

# Project wallet — the ONLY signing secret. Lives solely in `.env` (git-ignored).
# Never referenced in logs or printed by any module in this repo.
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")