"""Smart-match scoring/ranking and embedding similarity (no network)."""

import numpy as np

from models import SmartMatchHistoryRow
from name_similarity import (
    find_similar_names,
    fold_for_match,
    get_smart_match_candidates,
    levenshtein_similarity,
    normalize_phone_for_match,
    phone_matchable,
)


def test_levenshtein_similarity_bounds():
    assert levenshtein_similarity("abc", "abc") == 1.0
    assert levenshtein_similarity("abc", "xyz") == 0.0
    assert 0.0 < levenshtein_similarity("abc", "abd") < 1.0


def test_phone_normalization():
    assert normalize_phone_for_match("03-1234 (5678)") == "0312345678"
    assert phone_matchable("03-1234-5678") is True
    assert phone_matchable("123") is False


def test_smart_match_name_hit_is_quick_apply():
    history = [SmartMatchHistoryRow(extracted="Starbucks Shibuya", extracted_phone="", confirmed="Starbucks Shibuya")]
    out = get_smart_match_candidates("Starbucks Shibuya", "", history)
    assert len(out) == 1
    assert out[0].confirmed_name == "Starbucks Shibuya"
    assert out[0].name_score == 1.0
    assert out[0].quick_apply is True


def test_fold_for_match_ignores_case():
    assert fold_for_match("McDonald's KLCC") == fold_for_match("MCDONALD'S KLCC")


def test_smart_match_ignores_capitalization():
    """The OCR shouts a name one run and title-cases it the next; both are the same shop."""
    history = [SmartMatchHistoryRow(extracted="Starbucks Shibuya", extracted_phone="", confirmed="Starbucks Shibuya")]
    out = get_smart_match_candidates("STARBUCKS SHIBUYA", "", history)
    assert len(out) == 1
    assert out[0].name_score == 1.0
    assert out[0].quick_apply is True
    assert out[0].confirmed_name == "Starbucks Shibuya"  # offered back in the spelling that was confirmed


def test_smart_match_merges_confirmed_names_differing_only_in_case():
    """One entry per name in the list, in the spelling that was confirmed most often."""
    history = [
        SmartMatchHistoryRow(extracted="tealive klcc", extracted_phone="", confirmed="Tealive KLCC"),
        SmartMatchHistoryRow(extracted="TEALIVE KLCC", extracted_phone="", confirmed="TEALIVE KLCC"),
        SmartMatchHistoryRow(extracted="Tealive KLCC", extracted_phone="", confirmed="Tealive KLCC"),
    ]
    out = get_smart_match_candidates("Tealive KLCC", "", history)
    assert [c.confirmed_name for c in out] == ["Tealive KLCC"]
    assert out[0].name_score == 1.0


def test_smart_match_phone_boost_when_name_empty():
    history = [SmartMatchHistoryRow(extracted="some shop", extracted_phone="0312345678", confirmed="Real Shop")]
    out = get_smart_match_candidates("", "03-1234-5678", history)
    assert len(out) == 1
    assert out[0].phone_score == 1.0
    assert out[0].quick_apply is True


def test_smart_match_excludes_low_similarity():
    history = [SmartMatchHistoryRow(extracted="Starbucks Shibuya", extracted_phone="", confirmed="Starbucks Shibuya")]
    assert get_smart_match_candidates("zzzzzzzzzz", "", history) == []


def test_smart_match_ranked_by_combined_score():
    history = [
        SmartMatchHistoryRow(extracted="Family Mart KL", extracted_phone="", confirmed="Family Mart KL"),
        SmartMatchHistoryRow(extracted="Family Mart SS2", extracted_phone="", confirmed="Family Mart SS2"),
    ]
    out = get_smart_match_candidates("Family Mart KL", "", history)
    assert out[0].confirmed_name == "Family Mart KL"  # exact match ranks first
    assert out[0].combined_score >= out[-1].combined_score


def test_find_similar_names_uses_cosine_threshold():
    cached_names = ["q", "a", "b"]
    matrix = np.array([[1.0, 0.0], [1.0, 0.01], [0.0, 1.0]], dtype=np.float32)
    out = find_similar_names("q", ["a", "b"], cached_names, matrix, threshold=0.05)
    assert [name for name, _dist in out] == ["a"]
