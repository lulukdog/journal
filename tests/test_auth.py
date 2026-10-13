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


# ---------- 登录限流 ----------
# 口令可能是用户自己选的短口令，真正的防线是这里。


def test_no_lockout_before_threshold():
    ip = "10.0.0.1"
    auth.clear_failures(ip)
    for _ in range(auth.LOCKOUT_AFTER - 1):
        auth.record_failure(ip)
    assert auth.lockout_remaining(ip) == 0


def test_lockout_after_threshold():
    ip = "10.0.0.2"
    auth.clear_failures(ip)
    for _ in range(auth.LOCKOUT_AFTER):
        auth.record_failure(ip)
    assert auth.lockout_remaining(ip) > 0


def test_success_clears_failures():
    ip = "10.0.0.3"
    auth.clear_failures(ip)
    for _ in range(auth.LOCKOUT_AFTER):
        auth.record_failure(ip)
    auth.clear_failures(ip)
    assert auth.lockout_remaining(ip) == 0


def test_lockout_is_per_ip():
    a, b = "10.0.0.4", "10.0.0.5"
    auth.clear_failures(a)
    auth.clear_failures(b)
    for _ in range(auth.LOCKOUT_AFTER):
        auth.record_failure(a)
    assert auth.lockout_remaining(a) > 0
    assert auth.lockout_remaining(b) == 0, "不能因为别人被锁就把我也锁了"


def test_old_failures_expire(monkeypatch):
    """锁是滑动的：十五分钟前的失败不该还算数。"""
    ip = "10.0.0.6"
    auth.clear_failures(ip)

    clock = {"t": 1000.0}
    monkeypatch.setattr(auth, "_now", lambda: clock["t"])

    for _ in range(auth.LOCKOUT_AFTER):
        auth.record_failure(ip)
    assert auth.lockout_remaining(ip) > 0

    clock["t"] += auth.LOCKOUT_SECONDS + 1
    assert auth.lockout_remaining(ip) == 0, "窗口滑过去就该放行"
