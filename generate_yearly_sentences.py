#!/usr/bin/env python3
"""HSK 4급 하루 1문장 — 6개월치 문장 일괄 생성"""

import argparse
import logging
import sys
from datetime import datetime

from sentence_plan import YEARLY_MONTHS, ensure_yearly_plan, iter_plan_months, load_anchor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="HSK 4급 문장 6개월치 미리 생성")
    parser.add_argument("--year", type=int, help="시작 연도 (기본: 올해)")
    parser.add_argument("--month", type=int, help="시작 월 (기본: 이번 달)")
    parser.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")
    args = parser.parse_args()

    now = datetime.now()
    start_year = args.year or now.year
    start_month = args.month or now.month

    months = iter_plan_months(start_year, start_month)
    end_y, end_m = months[-1]
    print(f"생성 범위: {start_year}-{start_month:02d} ~ {end_y}-{end_m:02d} ({YEARLY_MONTHS}개월)")

    if not ensure_yearly_plan(start_year, start_month, force=args.force):
        print("일부 월 생성 실패", file=sys.stderr)
        return 1

    anchor = load_anchor()
    if anchor:
        print(f"완료: plan_anchor.json 저장됨 ({anchor['created_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
