import pytest

from gradebook.grades import average, letter, summarise


def test_average_of_scores():
    assert average([90, 80, 70]) == 80


def test_average_rejects_empty():
    with pytest.raises(ValueError):
        average([])


@pytest.mark.parametrize("score,grade", [
    (95, "A"), (85, "B"), (75, "C"), (65, "D"), (10, "F"), (90, "A"),
])
def test_letter_bands(score, grade):
    assert letter(score) == grade


def test_summarise_is_sorted_by_name():
    rows = summarise({"bea": [70, 80], "abe": [90, 100]})
    assert [r["name"] for r in rows] == ["abe", "bea"]
    assert rows[0]["average"] == 95.0 and rows[0]["grade"] == "A"
