"""Natural-language local entry point using the deterministic Planner fallback."""

from __future__ import annotations

import argparse
import json

from styleforge.common.console import configure_utf8_console
from styleforge.core.config import Settings
from styleforge.core.request_parser import parse_request
from styleforge.services.presentation import present_result
from styleforge.services.recommendation import recommend_for_user


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=str, default=settings.database_dsn)
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--request", required=True)
    parser.add_argument("--max-results", type=int, default=3)
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    task = parse_request(args.user_id, args.request, args.max_results)
    result = recommend_for_user(args.database, task)
    payload = {
        "mode": "rule_only",
        "request": args.request,
        "parsed_task": task.to_dict(),
        "result": present_result(args.database, result),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

