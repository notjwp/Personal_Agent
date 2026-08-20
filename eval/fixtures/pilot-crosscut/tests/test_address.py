from clean.address import clean_name, clean_postcode


def test_postcode_is_upper_and_unspaced():
    assert clean_postcode("sw1a 1aa") == "SW1A1AA"


def test_name_is_title_cased():
    assert clean_name("  ada   LOVELACE ") == "Ada Lovelace"
