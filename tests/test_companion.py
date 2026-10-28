"""情绪陪伴：听得出才说话，没有分数，没有诊断。"""

from app import companion


def test_quiet_on_ordinary_day():
    mood = companion.reflect("中午吃了面条，下午开了个会")
    assert mood["tone"] is None
    assert mood["line"] is None
    assert mood["heard"] == []
    assert "score" not in mood


def test_hears_each_everyday_tone():
    samples = {
        "tired": "今天真的好累",
        "stressed": "这周压力有点大",
        "sad": "有点难过，说不上来",
        "lonely": "晚上特别孤单",
        "anxious": "心里一直慌",
        "happy": "今天挺开心的",
    }
    for tone, text in samples.items():
        mood = companion.reflect(text)
        assert mood["tone"] == tone, text
        assert mood["line"]
        assert "抑郁" not in mood["line"]
        assert "你应该" not in mood["line"]
        assert "score" not in mood


def test_unhappy_is_not_happy():
    mood = companion.reflect("今天不开心")
    assert mood["tone"] == "sad"
    assert mood["tone"] != "happy"


def test_embedded_words_are_not_feelings():
    assert companion.reflect("这个项目积累了很多经验")["tone"] is None
    assert companion.reflect("这件事有点麻烦")["tone"] is None


def test_heavy_phrase_is_company_not_a_diagnosis():
    mood = companion.reflect("有时候觉得活着没意思")
    assert mood["tone"] == "heavy"
    assert "抑郁" not in mood["line"]
    assert "人" in mood["line"]
    assert "score" not in mood
    assert "mentions" not in companion.for_api(None, ["有时候觉得活着没意思"])


def test_for_api_prefers_card_line_after_close():
    card = {"companion": "先歇会儿，我在。"}
    mood = companion.for_api(card, ["今天好累"])
    assert mood["line"] == "先歇会儿，我在。"
    assert mood["source"] == "card"
    assert mood["tone"] == "tired"


def test_for_api_live_before_card_exists():
    mood = companion.for_api({"companion": None}, ["今天好累"])
    assert mood["source"] == "live"
    assert "歇" in mood["line"] or "累" in mood["line"]


def test_model_lecture_is_dropped():
    assert companion.usable_model_line("你应该调节一下抑郁情绪") is None
    assert companion.usable_model_line("你的焦虑水平偏高，打分 3 分") is None
    assert companion.usable_model_line("先歇会儿，我在。") == "先歇会儿，我在。"


def test_ask_note_only_when_there_is_a_feeling():
    assert companion.ask_note("我该不该换工作") is None
    note = companion.ask_note("今天好累，不想说话")
    assert note
    assert "接住" in note
    assert "打分" in note
    heavy = companion.ask_note("不想活了")
    assert "信得过的人" in heavy
    assert "抑郁" in heavy  # 是禁令，不是诊断
