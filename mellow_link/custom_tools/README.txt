# custom_tools (Phase 4: 동적 도구 확장)

이 폴더의 .py 파일은 DynamicToolRegistry에 의해 hot-reload 됩니다.
에이전트가 propose_new_tool로 제안한 도구가 검증 통과 시 여기에 저장됩니다.

- 파일명 = 도구(함수) 이름 (예: my_helper.py -> my_helper 함수 정의)
- 각 파일은 반드시 파일명과 동일한 이름의 callable 한 함수를 정의해야 합니다.
lun