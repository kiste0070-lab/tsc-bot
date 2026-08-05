from text_utils import normalize_chinese_lines, strip_markdown
from sentence_plan import check_duplicate


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


def test_strip_bold_and_asterisk_markers():
    text = "1.  **地方 (dìfang)**\n    *   **의미:** 곳, 장소"
    assert strip_markdown(text) == "1. 地方 (dìfang)\n의미: 곳, 장소"


def test_strip_markdown_keeps_content_text():
    text = "这个地方的风景很美，值得一去。"
    assert strip_markdown(text) == text


def test_strip_markdown_removes_heading_and_hr():
    text = "### 제목\n내용\n\n---\n\n아래 내용"
    assert strip_markdown(text) == "제목\n내용\n\n\n\n아래 내용"


def test_strip_markdown_removes_inline_code_backticks():
    text = "코드 `print('hi')` 예시"
    assert strip_markdown(text) == "코드 print('hi') 예시"


def test_strip_markdown_removes_bullet_list_markers():
    text = "- 첫번째\n- 두번째"
    assert strip_markdown(text) == "첫번째\n두번째"


def test_strip_markdown_empty_unchanged():
    assert strip_markdown("") == ""


def test_strip_markdown_removes_link_keeps_label():
    text = "문서 [여기](https://example.com) 참고"
    assert strip_markdown(text) == "문서 여기 참고"


def test_strip_markdown_removes_italic_underscores():
    text = "_강조된 단어_"
    assert strip_markdown(text) == "강조된 단어"


def test_check_duplicate_detects_exact_match():
    existing = ["请你把这份报告打印出来。"]
    new = ["请你把这份报告打印出来。", "新句子。"]
    assert check_duplicate(new, existing) == ["请你把这份报告打印出来。"]


def test_check_duplicate_case_insensitive_trim():
    existing = ["  请你把这份报告打印出来。  "]
    new = ["请你把这份报告打印出来。"]
    assert check_duplicate(new, existing) == ["请你把这份报告打印出来。"]


def test_check_duplicate_no_false_positive():
    existing = ["请你把这份报告打印出来。"]
    new = ["请你把这份报告打印。", "完全不同句子。"]
    assert check_duplicate(new, existing) == []
