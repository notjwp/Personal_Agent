from textstats.report import banner, summary


def test_summary_names_the_count():
    assert summary("doc", 3) == "doc: 3 words"


def test_banner_underlines_the_title():
    assert banner("hi").splitlines()[1] == "=="
