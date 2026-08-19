from subtitles.parse import parse

RAW = (
    "1" + chr(10) + "00:00:01,000 --> 00:00:02,000" + chr(10) + "hello" + chr(10)
    + chr(10)
    + "2" + chr(10) + "00:00:03,000 --> 00:00:04,000" + chr(10) + "world" + chr(10)
)


def test_parse_returns_one_cue_per_block():
    assert len(parse(RAW)) == 2


def test_parse_reads_index_and_text():
    first = parse(RAW)[0]
    assert first.index == 1
    assert first.text == "hello"
