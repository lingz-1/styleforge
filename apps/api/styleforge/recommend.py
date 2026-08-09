"""Command-line entry point for the deterministic recommendation baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from styleforge.common.console import configure_utf8_console
from styleforge.core.config import Settings
from styleforge.core.schemas import TaskSpec
from styleforge.services.presentation import present_result
from styleforge.services.recommendation import recommend_for_user


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--user-id", default="demo-user")
    parser.add_argument("--occasion", default="daily")
    parser.add_argument("--required-slots", type=_csv, default=("top", "bottom", "footwear"))
    parser.add_argument("--preferred-colors", type=_csv, default=())
    parser.add_argument("--excluded-colors", type=_csv, default=())
    parser.add_argument("--excluded-item-ids", type=_csv, default=())
    parser.add_argument("--max-results", type=int, default=3)
    return parser


def main() -> None:
    configure_utf8_console()
    args = build_parser().parse_args()
    task = TaskSpec(
        user_id=args.user_id,
        occasion=args.occasion,
        required_slots=args.required_slots,
        excluded_colors=args.excluded_colors,
        excluded_item_ids=args.excluded_item_ids,
        preferred_colors=args.preferred_colors,
        max_results=args.max_results,
    )
    result = recommend_for_user(args.database, task)
    print(json.dumps(present_result(args.database, result), ensure_ascii=False, indent=2))



if __name__ == "__main__":
    main()
