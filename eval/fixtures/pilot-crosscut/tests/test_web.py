from clean.web import clean_slug, clean_url


def test_url_loses_its_trailing_slash():
    assert clean_url("HTTPS://Example.com/Docs/") == "https://example.com/docs"


def test_slug_uses_hyphens():
    assert clean_slug("Hello World") == "hello-world"
