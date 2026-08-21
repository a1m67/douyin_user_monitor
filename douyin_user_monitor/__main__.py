from __future__ import annotations

import argparse
import json

from douyin_user_monitor.maintenance import (
    backup_database,
    database_stats,
    doctor_database,
    latest_backup,
    restore_database,
    verify_backup,
)
from douyin_user_monitor.short_drama_settings import load_short_drama_settings


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m douyin_user_monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    verify = sub.add_parser("backup-verify")
    verify.add_argument("--file", type=str)
    restore = sub.add_parser("restore")
    restore.add_argument("--from", dest="source", required=True)
    restore.add_argument("--dry-run", action="store_true")
    restore.add_argument("--yes", action="store_true")
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
    if args.command == "backup-verify":
        path = latest_backup(settings.database_path) if not args.file else args.file
        report = verify_backup(path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if args.command == "restore":
        report = restore_database(
            settings.database_path,
            source_path=args.source,
            dry_run=args.dry_run,
            confirmed=args.yes,
            retention_count=settings.backup_retention_count,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "db-stats":
        print(json.dumps(database_stats(settings.database_path, checkpoint=args.checkpoint), ensure_ascii=False, indent=2))
        return 0
    report = doctor_database(settings.database_path, repair=args.repair)
    print(json.dumps({"ok": report.ok, "repaired": report.repaired, "checks": report.checks}, ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
