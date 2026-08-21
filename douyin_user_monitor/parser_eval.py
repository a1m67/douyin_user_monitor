"""Offline exact-match evaluation for the committed parser golden corpus."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from douyin_user_monitor.parsers.episode_parser import EpisodeParser


DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "parser_golden.json"
EVALUATED_FIELDS = ("status", "show_title", "season_number", "episode_number", "content_type")


def evaluate_parser_golden(path: str | Path = DEFAULT_FIXTURE_PATH) -> dict[str, Any]:
    fixture_path = Path(path)
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("parser golden fixture must contain a JSON array")
    parser = EpisodeParser(llm_backend=None)
    failures: list[dict[str, Any]] = []
    field_passes = {field: 0 for field in EVALUATED_FIELDS}
    for case in cases:
        actual = _evaluate_case(parser, case)
        expected = dict(case.get("expected") or {})
        differences: dict[str, dict[str, Any]] = {}
        for field in EVALUATED_FIELDS:
            expected_value = expected.get(field)
            actual_value = actual[field]
            if actual_value == expected_value:
                field_passes[field] += 1
            else:
                differences[field] = {"expected": expected_value, "actual": actual_value}
        if differences:
            failures.append({"id": str(case.get("id") or ""), "differences": differences})
    total = len(cases)
    return {
        "ok": not failures,
        "fixture": fixture_path.name,
        "cases": total,
        "passed": total - len(failures),
        "failed": len(failures),
        "field_accuracy": {
            field: (field_passes[field] / total if total else 1.0)
            for field in EVALUATED_FIELDS
        },
        "failures": failures,
    }


def format_parser_eval(report: Mapping[str, Any]) -> str:
    lines = [
        f"cases: {report['cases']}",
        f"passed: {report['passed']}",
        f"failed: {report['failed']}",
        "field accuracy:",
    ]
    for field, accuracy in dict(report["field_accuracy"]).items():
        lines.append(f"  {field}: {float(accuracy):.2%}")
    for failure in report.get("failures") or ():
        lines.append(f"failure {failure['id']}: {json.dumps(failure['differences'], ensure_ascii=False)}")
    return "\n".join(lines)


def _evaluate_case(parser: EpisodeParser, case: Mapping[str, Any]) -> dict[str, Any]:
    result = parser.parse(
        display_title=str(case.get("title") or ""),
        description=str(case.get("description") or ""),
        hashtags=_string_list(case.get("hashtags")),
        account_nickname=str(case.get("account_nickname") or "AI剧场"),
        known_shows=_mapping_list(case.get("known_shows")),
        recent_account_videos=_mapping_list(case.get("recent_account_videos")),
        recent_account_matches=_mapping_list(case.get("recent_account_matches")),
        account_show_candidates=_mapping_list(case.get("account_show_candidates")),
        text_sources=dict(case.get("text_sources") or {}),
    )
    return {field: getattr(result, field) for field in EVALUATED_FIELDS}


def _string_list(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _mapping_list(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))
