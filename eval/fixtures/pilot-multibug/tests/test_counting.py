from textstats.counting import word_count, words


def test_words_ignores_runs_of_whitespace():
    assert words("a  b") == ["a", "b"]


def test_word_count_counts_real_words():
    assert word_count("one  two   three") == 3
