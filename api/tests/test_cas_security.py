from app.core.security.cas import hmac_cas_key


def test_hmac_cas_key_is_deterministic():
    sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    key1 = hmac_cas_key(sha256)
    key2 = hmac_cas_key(sha256)
    assert key1 == key2
    assert key1.startswith("upload:cas:")


def test_hmac_cas_key_varies_by_hash():
    sha1 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    sha2 = "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    assert hmac_cas_key(sha1) != hmac_cas_key(sha2)
