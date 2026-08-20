from routing import tokens


def test_words_ignores_runs_of_whitespace():
    assert tokens.words("a  b") == ["a", "b"]
