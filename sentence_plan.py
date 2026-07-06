"""HSK 4급 하루 1문장 — 연간·월간 계획 생성/조회"""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_1 = os.getenv("GEMINI_MODEL_PRIMARY", "gemini-2.5-flash")
GEMINI_MODEL_2 = os.getenv("GEMINI_MODEL_SECONDARY", "gemini-2.5-flash-lite")
MODELS = [GEMINI_MODEL_1, GEMINI_MODEL_2]

BASE_DIR = Path(__file__).resolve().parent
SENTENCES_DIR = BASE_DIR / "Daily_Sentences"
ANCHOR_FILE = SENTENCES_DIR / "plan_anchor.json"

YEARLY_MONTHS = 12

DATE_PATTERN = re.compile(
    r"###\s*(\d{4})[-\s년]*0?(\d{1,2})[-\s월]*0?(\d{1,2})[-\s일]*"
)
SENTENCE_PATTERN = re.compile(r"^문장\s*:\s*(.+)$")
TOPIC_PATTERN = re.compile(r"^주제\s*:\s*(.+)$")
MEMO_PATTERN = re.compile(r"^메모\s*:\s*(.+)$")

client = genai.Client(api_key=GEMINI_KEY)


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def iter_plan_months(
    start_year: int, start_month: int, count: int = YEARLY_MONTHS
) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    y, m = start_year, start_month
    for _ in range(count):
        months.append((y, m))
        y, m = next_month(y, m)
    return months


def get_monthly_filepath(year: int, month: int) -> Path:
    return SENTENCES_DIR / f"{year}_{month:02d}.md"


def monthly_plan_exists(year: int, month: int) -> bool:
    path = get_monthly_filepath(year, month)
    if not path.exists():
        return False
    return bool(SENTENCE_PATTERN.search(path.read_text(encoding="utf-8")))


def load_anchor() -> dict | None:
    if not ANCHOR_FILE.exists():
        return None
    try:
        return json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"anchor 파일 읽기 실패: {e}")
        return None


def save_anchor(start_year: int, start_month: int, months: int = YEARLY_MONTHS):
    plan_months = iter_plan_months(start_year, start_month, months)
    end_year, end_month = plan_months[-1]
    data = {
        "start_year": start_year,
        "start_month": start_month,
        "end_year": end_year,
        "end_month": end_month,
        "months": months,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "hsk_level": 4,
    }
    SENTENCES_DIR.mkdir(parents=True, exist_ok=True)
    ANCHOR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def get_existing_sentences() -> list[str]:
    existing: list[str] = []
    if not SENTENCES_DIR.exists():
        return existing
    for filepath in SENTENCES_DIR.glob("*.md"):
        try:
            for line in filepath.read_text(encoding="utf-8").splitlines():
                m = SENTENCE_PATTERN.match(line.strip())
                if m:
                    existing.append(m.group(1).strip())
        except Exception as e:
            logger.warning(f"파일 읽기 오류 {filepath.name}: {e}")
    return existing


def check_duplicate(new_sentences: list[str], existing: list[str]) -> list[str]:
    existing_set = set(existing)
    return [s for s in new_sentences if s.strip() in existing_set]


def _parse_sentences_from_content(content: str) -> list[str]:
    sentences = []
    for line in content.splitlines():
        m = SENTENCE_PATTERN.match(line.strip())
        if m:
            sentences.append(m.group(1).strip())
    return sentences


def generate_monthly_sentences(year: int, month: int, force: bool = False) -> bool:
    filepath = get_monthly_filepath(year, month)
    if monthly_plan_exists(year, month) and not force:
        logger.info(f"이미 존재: {filepath.name}")
        return True
    if force and filepath.exists():
        filepath.unlink()

    SENTENCES_DIR.mkdir(parents=True, exist_ok=True)
    existing = get_existing_sentences()
    existing_context = ""
    if existing:
        existing_context = "\n[이미 사용된 문장 - 절대 중복 금지]\n" + "\n".join(
            existing[:200]
        )

    num_days = calendar.monthrange(year, month)[1]
    prompt = f"""너는 중국어 회화 학습 콘텐츠 제작 전문가야.
{year}년 {month}월의 '하루 1문장' 학습 계획을 만들어줘.

[요청]
- {year}년 {month}월 1일~{num_days}일, 매일 실생활에서 자주 쓰이는 중국어 문장 1개씩 (총 {num_days}개)
- **HSK 4급 수준** 문장 (어휘·문법 모두 HSK 4 범위, 약 10~20자)
- HSK 4급 문법 활용 권장: 把/被, 不但…而且, 虽然…但是, 越…越…, 连…都, 결과보어 등
- HSK 4급 어휘 위주, 1~3급만으로 된 너무 단순한 문장은 피할 것
- 직장, 여행, 건강, 취미, 사회생활, 감정·의견 표현 등 HSK 4에 맞는 주제
- 같은 달·기존 문장과 절대 중복 금지{existing_context}

[출력 형식 - 이 형식만 사용]
### YYYY-MM-DD
한자(병음, 한글 뜻)
외워야 할 항목: [단어1 — 품사(한글 뜻)], [숙어 — 뜻], [문장형식 — 설명]

### YYYY-MM-DD
...

[규칙]
1. 문장 필드는 중국어만
2. 모든 문장은 HSK 4급 학습자가 이해·암기하기 적합한 난이도
3. {year}년 {month}월 모든 날짜를 빠짐없이 포함
4. 설명·인사말 없이 위 형식만 출력
5. 날짜 헤더는 ### YYYY-MM-DD 형식
"""

    for i, model_id in enumerate(MODELS):
        for attempt in range(3):
            try:
                logger.info(f"[{model_id}] {year}-{month:02d} 생성 시도 {attempt + 1}/3")
                response = client.models.generate_content(model=model_id, contents=prompt)
                plan_content = response.text.strip()
                new_sentences = _parse_sentences_from_content(plan_content)

                if len(new_sentences) < num_days:
                    logger.warning(
                        f"문장 수 부족: {len(new_sentences)}/{num_days}, 재시도"
                    )
                    prompt += f"\n\n[오류] {num_days}일치 문장이 필요합니다. 현재 {len(new_sentences)}개만 생성됨."
                    continue

                dupes = check_duplicate(new_sentences, existing)
                if dupes:
                    prompt += "\n\n[중복 문장 - 사용 금지]\n" + "\n".join(dupes[:20])
                    continue

                filepath.write_text(
                    f"# {year}년 {month}월 하루 1문장 학습 (HSK 4급)\n\n{plan_content}\n",
                    encoding="utf-8",
                )
                logger.info(f"완료: {filepath.name} ({len(new_sentences)}문장)")
                return True
            except Exception as e:
                logger.error(f"[{model_id}] 생성 오류: {e}")
                if attempt < 2:
                    time.sleep(60)
                elif i < len(MODELS) - 1:
                    time.sleep(120)
                break
    return False


def yearly_plan_complete(start_year: int, start_month: int) -> bool:
    return all(
        monthly_plan_exists(y, m)
        for y, m in iter_plan_months(start_year, start_month)
    )


def ensure_yearly_plan(
    start_year: int | None = None,
    start_month: int | None = None,
    force: bool = False,
) -> bool:
    now = datetime.now()
    if start_year is None:
        start_year = now.year
    if start_month is None:
        start_month = now.month

    anchor = load_anchor()
    if anchor and not force:
        sy, sm = anchor["start_year"], anchor["start_month"]
        if yearly_plan_complete(sy, sm):
            logger.info(f"연간 계획 완료: {sy}-{sm:02d} ~ {anchor['end_year']}-{anchor['end_month']:02d}")
            return True
        start_year, start_month = sy, sm

    logger.info(f"연간 문장 생성 시작: {start_year}-{start_month:02d}부터 {YEARLY_MONTHS}개월")
    ok = True
    for y, m in iter_plan_months(start_year, start_month):
        if not generate_monthly_sentences(y, m, force=force):
            logger.error(f"실패: {y}-{m:02d}")
            ok = False
    if ok:
        save_anchor(start_year, start_month)
    return ok


def get_today_sentence(year: int, month: int, day: int) -> dict | None:
    filepath = get_monthly_filepath(year, month)
    if not filepath.exists():
        return None
    try:
        content = filepath.read_text(encoding="utf-8")
        exact = f"### {year}-{month:02d}-{day:02d}"
        if exact in content:
            start = content.find(exact)
        else:
            flex = re.compile(
                rf"###\s*{year}[-\s년]*0?{month}[-\s월]*0?{day}[-\s일]*"
            )
            m2 = flex.search(content)
            if not m2:
                return None
            start = m2.start()

        next_hdr = content.find("\n### ", start + 1)
        section = content[start:] if next_hdr == -1 else content[start:next_hdr]

        result: dict = {}
        for line in section.splitlines():
            line = line.strip()
            for pat, key in (
                (SENTENCE_PATTERN, "sentence"),
                (TOPIC_PATTERN, "topic"),
                (MEMO_PATTERN, "memo"),
            ):
                m = pat.match(line)
                if m:
                    result[key] = m.group(1).strip()
        return result if "sentence" in result else None
    except Exception as e:
        logger.error(f"오늘 문장 읽기 오류: {e}")
        return None
