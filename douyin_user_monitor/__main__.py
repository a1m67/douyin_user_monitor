from __future__ import annotations

import argparse
import json

from douyin_user_monitor.maintenance import backup_database, database_stats, doctor_database
from douyin_user_monitor.short_drama_settings import load_short_drama_settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m douyin_user_monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repair", action="store_true")
    stats = sub.add_parser("db-stats")
    stats.add_argument(
        "--checkpoint",
        action="store_true",
        help="run an explicit WAL truncate checkpoint before collecting statistics",
    )
    args = parser.parse_args()
    settings = load_short_drama_settings()
    if args.command == "backup":
        path = backup_database(settings.database_path, retention_count=settings.backup_retention_count)
        print(path)
        return 0
    if args.command == "db-stats":
        print(json.dumps(database_stats(settings.database_path, checkpoint=args.checkpoint), ensure_ascii=False, indent=2))
        return 0
    report = doctor_database(settings.database_path, repair=args.repair)
    print(json.dumps({"ok": report.ok, "repaired": report.repaired, "checks": report.checks}, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
