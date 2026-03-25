# rebuild_assistant 샘플 세트

이 압축 파일은 `rebuild_assistant` V0 테스트용 샘플입니다.

## 포함 파일
- `goal.txt`: 재구성 목표
- `constraints.txt`: 유지해야 할 제약조건
- `legacy.jsp`: JSP 화면 샘플
- `service.java`: Java 서비스 샘플
- `query.sql`: 조회 SQL 샘플
- `schema.sql`: DB 스키마 샘플

## 샘플 의도
이 샘플은 "예외사항 지옥" 유형의 레거시 기능을 가정합니다.

포인트:
- UI/JSP에 검증 규칙이 섞여 있음
- 서비스에 권한/상태별 예외 규칙이 섞여 있음
- SQL에도 필터 분기와 상태 범위 규칙이 포함됨
- 단일 기능이지만 예외 규칙이 여러 레이어에 퍼져 있음

## 추천 입력 방식
1. `goal.txt` 내용을 Goal에 입력
2. `legacy.jsp` + `service.java`를 Legacy source code에 붙여넣기
3. `schema.sql`을 Database schema에 입력
4. `query.sql`을 SQL queries에 입력
5. `constraints.txt`를 Constraints에 입력

또는 파일들을 문서 업로드로 올리고, Goal/Constraints만 직접 넣어도 됩니다.
