from textstats.averages import mean_word_length


def test_mean_word_length_averages_over_words():
    """Two words of four letters each average four, not per character."""
    assert mean_word_length("abcd efgh") == 4.0


def test_empty_text_averages_zero():
    assert mean_word_length("") == 0.0
