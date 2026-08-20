from textstats.normalise import slugify


def test_slug_is_lowercase():
    assert slugify("Hello World") == "hello-world"


def test_slug_drops_punctuation():
    assert slugify("Hello, World!") == "hello-world"
