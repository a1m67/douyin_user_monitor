import random

MIN_INTERVAL_HOURS = 0.0083333333
MIN_COVERAGE_HOURS = 0.1
LOOP_ERROR_RETRY_SECONDS = 10
MODE_INTERVAL = "interval"
MODE_COVERAGE = "coverage"


def validate_options(
    *,
    mode: str,
    interval_hours: float,
    coverage_hours: float,
) -> None:
    if mode not in {MODE_INTERVAL, MODE_COVERAGE}:
        raise ValueError("mode 仅支持 interval 或 coverage")
    if interval_hours < MIN_INTERVAL_HOURS:
        raise ValueError(f"监控间隔不能小于{MIN_INTERVAL_HOURS}小时")
    if coverage_hours < MIN_COVERAGE_HOURS:
        raise ValueError(f"coverage_hours 不能小于 {MIN_COVERAGE_HOURS}")


def build_interval_gaps(user_count: int) -> list[float]:
    _ = user_count
    return []


def choose_coverage_delay(*, remaining_seconds: float, future_gap_count: int) -> float:
    if remaining_seconds <= 0:
        return 0.0

    # Include the final tail gap to next cycle so expected delay stays near average.
    total_remaining_gaps = future_gap_count + 1
    if total_remaining_gaps <= 0:
        return 0.0
    if total_remaining_gaps == 1:
        return remaining_seconds

    ratio = random.betavariate(1.0, float(total_remaining_gaps - 1))
    return remaining_seconds * ratio
