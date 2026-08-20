from analytics import ratios


def test_mean_len_divides_by_count():
    assert ratios.mean_len(["abcd", "efgh"]) == 4.0
