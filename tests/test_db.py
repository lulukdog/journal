from app import db as db_mod


def test_init_db_creates_all_tables(conn):
    names = db_mod.table_names(conn)
    for expected in db_mod.TABLES:
        assert expected in names, f"缺少表 {expected}"


def test_init_db_is_idempotent(conn):
    db_mod.init_db(conn)
    db_mod.init_db(conn)
    assert set(db_mod.TABLES) <= db_mod.table_names(conn)


def test_events_unique_per_day_and_type(conn):
    """同一天同一种信号只能有一条 —— 这是收口任务幂等的基石。"""
    import sqlite3

    args = ("2026-10-13T23:30:00", "2026-10-13", "repeat", "{}")
    conn.execute(
        "INSERT INTO events (ts, day, signal_type, payload) VALUES (?,?,?,?)", args
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO events (ts, day, signal_type, payload) VALUES (?,?,?,?)", args
        )
        conn.commit()
        raise AssertionError("应当违反 UNIQUE(day, signal_type)")
    except sqlite3.IntegrityError:
        pass
