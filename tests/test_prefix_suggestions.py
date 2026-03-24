from brand_registry import build_prefix_suggestions


def test_prefix_suggestions_supersession_prefers_longer_equal_support_prefix():
    names = [
        "seven-eleven kl",
        "seven-eleven ampang",
        "seven-eleven puchong",
    ]
    suggestions = build_prefix_suggestions(
        names,
        boundary_only=False,
        max_length=12,
        min_length=5,
        min_count=2,
    )
    prefixes = [s.prefix for s in suggestions]
    assert "seven-elev" not in prefixes
    assert "seven-eleven" in prefixes


def test_prefix_suggestions_boundary_mode_keeps_clean_delimiter_cuts():
    names = ["family mart ss2", "family mart kl", "family mart pj"]
    suggestions = build_prefix_suggestions(
        names,
        boundary_only=True,
        max_length=24,
        min_length=3,
        min_count=2,
    )
    prefixes = [s.prefix for s in suggestions]
    assert "family mart" in prefixes
    assert "family m" not in prefixes


def test_prefix_suggestions_char_mode_allows_character_prefixes():
    names = ["tealive bukit bintang", "tealive pj", "tealive klcc"]
    suggestions = build_prefix_suggestions(
        names,
        boundary_only=False,
        max_length=8,
        min_length=5,
        min_count=2,
    )
    prefixes = [s.prefix for s in suggestions]
    assert "tealive" in prefixes


def test_prefix_suggestions_respects_thresholds_and_ranking():
    names = ["abc shop 1", "abc shop 2", "abc market 1", "abx market 2"]
    suggestions = build_prefix_suggestions(
        names,
        boundary_only=False,
        max_length=6,
        min_length=3,
        min_count=2,
    )
    assert suggestions
    assert suggestions[0].count >= suggestions[-1].count
    assert all(len(s.prefix) >= 3 for s in suggestions)
