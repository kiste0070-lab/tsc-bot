# TSC 봇 데일리 로그

## 2026-03-20

### ✅ 주요 변경사항
- **오답노트 자동저장 기능 추가** (`main.py`)
  - `save_wrong_note()` 함수 신규 구현
  - 봇의 한국어 첨삭/교정 응답이 있을 때만 자동 저장
  - 수업종료·문제설명·문제해석 등 특수 명령어는 저장 제외
- **월별 파일 구조로 변경**
  - 저장 경로: `wrong_notes/YYYYMM_wrong_notes.md`
  - 파일 내 날짜 헤더(`## YYYY-MM-DD`)로 날짜별 구분
- **httpx/httpcore INFO 로그 억제** (`main.py`)
  - 텔레그램 폴링 관련 반복 HTTP 로그 숨김 처리
- **GitHub Actions 자동 커밋 추가** (`daily_lesson.yml`)
  - 봇 실행 완료 후 `wrong_notes/` 변경사항을 자동 commit & push
  - `permissions: contents: write` 설정 추가
- **`wrong_notes/.gitkeep`** 추가 (빈 폴더 git 추적용)
- **설명서.md** 업데이트
  - GitHub Actions Secrets 설정 방법 추가
  - 오답노트 폴더 구조 설명 추가
  - 패키지명 `google-generativeai` → `google-genai` 수정

---
