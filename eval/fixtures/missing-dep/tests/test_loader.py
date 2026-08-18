from gradebook.loader import load_scores, save_scores


def test_round_trip(tmp_path):
    path = tmp_path / "scores.csv"
    records = {"abe": [90.0, 100.0], "bea": [70.0, 80.0]}
    save_scores(path, records)
    assert load_scores(path) == records


def test_load_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "scores.csv"
    path.write_text("# header\n\nabe,90,100\n", encoding="utf-8")
    assert load_scores(path) == {"abe": [90.0, 100.0]}
