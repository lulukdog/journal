from app import auth


def test_verify_token_accepts_match():
    assert auth.verify_token("abc123", "abc123") is True


def test_verify_token_rejects_mismatch():
    assert auth.verify_token("abc123", "abc124") is False
    assert auth.verify_token("abc", "abcd") is False


def test_verify_token_rejects_empty_and_none():
    assert auth.verify_token("", "abc") is False
    assert auth.verify_token(None, "abc") is False
    assert auth.verify_token("abc", "") is False


def test_new_token_is_long_and_unique():
    tokens = {auth.new_token() for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(t) >= 32 for t in tokens)
