"""Archive submission blockers."""

from validation import is_date_time_safe_for_archive


def test_valid_date_and_time():
    assert is_date_time_safe_for_archive("2025-03-15", "13:26") == (True, "")
    assert is_date_time_safe_for_archive("2025-03-15", "13:26:42") == (True, "")
    assert is_date_time_safe_for_archive("", "") == (True, "")


def test_invalid_date():
    ok, msg = is_date_time_safe_for_archive("2025-13-40", "13:26")
    assert ok is False and "YYYY-MM-DD" in msg


def test_invalid_time():
    ok, _ = is_date_time_safe_for_archive("2025-03-15", "ab:cd")
    assert ok is False


def test_too_many_time_parts():
    ok, _ = is_date_time_safe_for_archive("2025-03-15", "13:26:42:00")
    assert ok is False
