from clean.contacts import clean_email, clean_phone


def test_email_is_lowercased():
    assert clean_email("  Bob@Example.COM ") == "bob@example.com"


def test_phone_keeps_only_digits():
    assert clean_phone("+44 (20) 7946-0958") == "442079460958"
