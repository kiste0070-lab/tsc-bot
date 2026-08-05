"""텍스트 정규화 유틸 — Gemini 응답의 단어별 줄바꿈 문제 해결"""

import re

_CJK_IDEOGRAPH = re.compile(r"[\u4e00-\u9fff]")
_CJK_ONLY = re.compile(r"^[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\s]+$")
_SENTENCE_END = ("。", "！", "？")

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_MARKDOWN_HR = re.compile(r"^\s{0,3}((-{3,})|(\*{3,})|(_{3,}))\s*$")
_MARKDOWN_BULLET = re.compile(r"^\s*([-+*])\s+")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def strip_markdown(text: str) -> str:
    """Gemini 응답에서 마크다운 문법 기호를 제거해 순수 텍스트로 만든다."""
    if not text:
        return text
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    lines = []
    for line in text.splitlines():
        line = _MARKDOWN_HR.sub("", line)
        line = _MARKDOWN_BULLET.sub("", line)
        line = line.strip()
        lines.append(line)
    lines = [_MARKDOWN_HEADING.sub("", ln) for ln in lines]
    joined = "\n".join(lines)
    joined = joined.replace("**", "").replace("__", "")
    joined = joined.replace("_", "")
    joined = re.sub(r" {2,}", " ", joined)
    return joined


def _is_chinese_only(line: str) -> bool:
    if not line:
        return False
    if not _CJK_IDEOGRAPH.search(line):
        return False
    return bool(_CJK_ONLY.match(line))


def _ends_sentence(line: str) -> bool:
    return line.rstrip().endswith(_SENTENCE_END)


def normalize_chinese_lines(text: str) -> str:
    """단어 단위로 줄바꿈된 중국어 문장을 한 줄로 병합한다.

    - 한자만으로 이루어진 연속 라인 그룹에서, 종결 부호(。！？)로
      끝나지 않는 조각이 있으면 그룹 전체를 한 문장으로 합친다.
    - 각각 종결 부호로 끝나는 별개의 완전한 문장(예: 예문 나열)은
      그대로 두어 의도된 줄바꿈을 보존한다.
    """
    if not text:
        return text

    lines = text.splitlines()
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_chinese_only(line):
            run = [line.strip()]
            j = i + 1
            while j < len(lines) and _is_chinese_only(lines[j]):
                run.append(lines[j].strip())
                j += 1
            if len(run) > 1 and any(not _ends_sentence(l) for l in run):
                result.append("".join(run))
            else:
                result.extend(run)
            i = j
        else:
            result.append(line)
            i += 1
    return "\n".join(result)
