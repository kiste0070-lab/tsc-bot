from text_utils import normalize_chinese_lines


def test_merges_word_per_line_broken_sentence():
    text = "我\n把电脑\n关机了。"
    assert normalize_chinese_lines(text) == "我把电脑关机了。"


def test_merges_fragments_including_final_punctuation():
    text = "我\n把电脑\n关机了。\n"
    assert normalize_chinese_lines(text) == "我把电脑关机了。"


def test_does_not_merge_separate_complete_sentences():
    text = "我去过北京。\n他也去过上海。"
    assert normalize_chinese_lines(text) == "我去过北京。\n他也去过上海。"


def test_merges_fragment_with_following_complete_sentence():
    text = "我\n把电脑关机了。"
    assert normalize_chinese_lines(text) == "我把电脑关机了。"


def test_merges_without_final_punctuation():
    text = "我\n把电脑\n关机了"
    assert normalize_chinese_lines(text) == "我把电脑关机了"


def test_preserves_pinyin_line():
    text = "我\n把电脑\n关机了。\n(Wǒ bǎ diànnǎo guānjī le.)"
    assert normalize_chinese_lines(text) == "我把电脑关机了。\n(Wǒ bǎ diànnǎo guānjī le.)"


def test_preserves_korean_and_mixed_structure():
    text = "📌 오늘의 문장 (HSK 4급)\n我\n把电脑\n关机了。\n(Wǒ bǎ diànnǎo guānjī le.)\n저는 컴퓨터를 껐습니다."
    expected = (
        "📌 오늘의 문장 (HSK 4급)\n"
        "我把电脑关机了。\n"
        "(Wǒ bǎ diànnǎo guānjī le.)\n"
        "저는 컴퓨터를 껐습니다."
    )
    assert normalize_chinese_lines(text) == expected


def test_single_line_chinese_unchanged():
    text = "我把电脑关机了。"
    assert normalize_chinese_lines(text) == text


def test_empty_string_unchanged():
    assert normalize_chinese_lines("") == ""


def test_merge_preserves_inner_comma():
    text = "他\n工作很忙，\n没时间休息。"
    assert normalize_chinese_lines(text) == "他工作很忙，没时间休息。"
