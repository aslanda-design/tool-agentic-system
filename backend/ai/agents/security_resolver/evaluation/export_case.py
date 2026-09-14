"""Export a real resolution from your own dev DB as an evaluation case file
— useful for building a private eval set from your own actual ambiguous
resolutions. Written to cases_local/ (gitignored — see backend/.gitignore
and evaluation/README.md): a real resolution reflects your own holdings,
which is personal data.

    python -m ai.agents.security_resolver.evaluation.export_case 42 \\
        --expected-tool save_security_mapping --expected-symbol VUSA.L

The exported case's tool_fixtures start empty — fill them in by hand for
any tool call the agent might reasonably make beyond the candidates already
on the resolution (validate_listing for a symbol not already a candidate;
search_listings/lookup_isin if the right listing has to be found rather
than picked from what's given).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.common.jsonable import to_jsonable
from app.adapters.persistence.session import SessionLocal
from app.container import resolution_repo

LOCAL_CASES_DIR = Path(__file__).with_name("cases_local")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("resolution_id", type=int)
    parser.add_argument("--expected-tool", choices=["save_security_mapping", "flag_for_review"], required=True)
    parser.add_argument("--expected-symbol", default=None, help="Required when --expected-tool is save_security_mapping.")
    parser.add_argument("--tags", nargs="*", default=[])
    args = parser.parse_args()

    if args.expected_tool == "save_security_mapping" and not args.expected_symbol:
        parser.error("--expected-symbol is required when --expected-tool=save_security_mapping")

    db = SessionLocal()
    try:
        resolution = resolution_repo(db).get(args.resolution_id)
    finally:
        db.close()
    if resolution is None:
        raise SystemExit(f"Resolution {args.resolution_id} not found")

    case_id = f"resolution_{args.resolution_id}"
    case = {
        "id": case_id,
        "resolution": to_jsonable(resolution),
        "tool_fixtures": {"validate_listing": {}, "search_listings": {}, "lookup_isin": {}},
        "expected": {"final_tool": args.expected_tool, "symbol": args.expected_symbol},
        "tags": args.tags,
    }

    LOCAL_CASES_DIR.mkdir(exist_ok=True)
    out_path = LOCAL_CASES_DIR / f"{case_id}.json"
    out_path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    print("Fill in tool_fixtures by hand for anything the agent might look up beyond the given candidates.")


if __name__ == "__main__":
    main()
