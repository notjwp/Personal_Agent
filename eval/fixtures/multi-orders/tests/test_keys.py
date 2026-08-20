from orders import keys


def test_keyify_is_lowercase():
    assert keys.keyify(" Hello World ") == "hello_world"
