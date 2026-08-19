import pytest

from subtitles.io import load_file, load_many

ACCENTED = (
    "1" + chr(10)
    + "00:00:01,000 --> 00:00:02,000" + chr(10)
    + "Café déjà vu" + chr(10)
)


@pytest.fixture
def srt(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text(ACCENTED, encoding="utf-8")
    return p


def test_load_file_reads_accented_text(srt):
    """Subtitles are full of accents; reading one must not depend on luck."""
    assert "Café" in load_file(srt)


def test_load_many_reads_every_file(srt):
    """A second reader exists, and it has to work too."""
    out = load_many([srt, srt])
    assert len(out) == 2
    assert all("déjà" in text for text in out)
