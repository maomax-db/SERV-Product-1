"""Sector 0 test gate — prove the OpenServ / SERV plumbing with a trivial prompt.

Prints the RAW JSON response body (not just a summary), then the decoded text.

Usage:
    python agent-core/test_openserv.py
    python agent-core/test_openserv.py --model gpt-5.4-nano
    python agent-core/test_openserv.py --prompt "What is 2 + 2?"

Exit code 0 on success, 1 on failure. Requires OPENSERV_API_KEY (see README).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # allow flat sibling imports

from config import OPENSERV_BASE_URL, OPENSERV_MODEL  # noqa: E402
from serv import chat  # noqa: E402

TRIVIAL_PROMPT = (
    "Connectivity test. Reply with exactly the single word `pong` and "
    "nothing else — no punctuation, no prose."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Trivial SERV reasoning connectivity check.")
    parser.add_argument(
        "--model",
        default=OPENSERV_MODEL,
        help="SERV model id (default: OPENSERV_MODEL env value).",
    )
    parser.add_argument("--prompt", default=TRIVIAL_PROMPT, help="Prompt to send.")
    args = parser.parse_args()

    print(f"[agent-core] POST {OPENSERV_BASE_URL}/chat/completions")
    print(f"[agent-core] model        = {args.model}")
    print("[agent-core] request      = sending trivial test prompt ...\n")

    try:
        raw = chat(args.prompt, model=args.model)
    except Exception as exc:  # noqa: BLE001 - report any failure loudly
        print(f"[agent-core] FAILED: {exc}\n", file=sys.stderr)
        return 1

    print("[agent-core] RAW RESPONSE (verbatim):")
    print(json.dumps(raw, indent=2))

    message = raw["choices"][0]["message"]["content"]
    print("\n[agent-core] CONTENT:", message)
    print("[agent-core] OK — OpenServ API returned a valid response.")
    return 0


if __name__ == "__main__":
    sys.exit(main())