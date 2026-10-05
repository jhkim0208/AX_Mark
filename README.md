# AX Mark – 업적평가 · 보상 시뮬레이션

부서장이 부서원의 개인 평가등급(A~E)을 선택하면 차년도 연봉·인센티브와
전년 대비 증감액을 즉시 보여주고, 인사팀은 부서별 평가 현황을 한눈에 확인하는 웹앱입니다.

## 화면 구성

### 1. 평가 입력 (부서장, `/`)

| 영역 | 내용 |
|------|------|
| 상단 | 평가주기, 부서 선택, 평가자(부서장), 기본인상률, 인센티브 지급 등급 |
| 요약 | 입력 현황, 현재/예상 연봉 합계와 증감, 예상 인센티브 합계, 평균 인상률 |
| 표 | 사번 · 성명 · 직급 · **현재 연봉** · **평가등급(선택박스, 기본 공란)** · 성과인상률 · 총인상률 · **차년도 연봉** · 연봉 증감 · **예상 인센티브** · 총보상 증감 |
| 하단 | 상위평가(A·B) 비율 – 기준 40% 초과 시 ⚠ 초과, 등급별 인원 분포, 평가 제출 |

- 부서원 30~40명 기준으로 표 안에서 세로 스크롤되며 머리글은 고정됩니다.
- 등급을 선택하는 즉시 임시저장(DRAFT)되고 서버에서 재계산된 결과가 행에 반영됩니다.
- 제출하면 해당 부서 평가는 잠기며 수정할 수 없습니다.
- 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장 (예: 본부장 → 팀장·Lab장 평가)
- 직급: CL2(사원~대리급) · CL3(과장~차장급) · CL4(부장급)

### 2. 부서별 현황 (인사팀, `/hr`)

| 영역 | 내용 |
|------|------|
| 요약 | 평가 대상 인원, 평가 입력률, 제출 완료 부서, 전사 상위평가율(기준 40% · 초과 부서 수), 전사 평균 성과인상률 |
| 부서별 표 | 부서 · 부서장 · 인원 · 입력 현황 · **등급 분포(A~E 누적 막대)** · **상위평가율(기준 40% 선 표시, 초과 시 ⚠ 초과)** · **평균 성과인상률** · 평균 총인상률 · 상태 |
| 부서 클릭 → 팝업 | 부서 요약 + 부서원별 평가등급 · 상태 · 성과인상률 · 총인상률 · 현재/차년도 연봉 · 연봉 증감 |

- **상위평가율** = A·B 등급(`evaluation_grade.is_top_grade`) 인원 ÷ 평가 입력 인원.
  `evaluation_cycle.top_grade_ratio_limit`(현재 40%)를 **넘으면** 부서장·인사팀 화면에 '초과' 경고
- **평균 성과인상률** = 평가가 입력된 인원의 성과인상률 단순 평균
- 인사팀장처럼 부서장을 겸하는 경우 상단 메뉴로 두 화면을 오갈 수 있습니다.

## 인상률 로직

```
총인상률   = 기본인상률(전원 공통, comp_policy.base_up_rate)
           + 성과인상률(개인 평가등급으로만 결정, comp_rule.perf_raise_rate)
             A·B·C 양수 / D 동결(0) / E 음수  ← DB 트리거로 강제
차년 연봉  = 절사(현재 연봉 × (1 + 총인상률)) → 직급 연봉 밴드로 보정
인센티브   = A·B 등급만 지급, C·D·E 는 0  ← 세부 산식은 확정 전 임시
```

샘플 데이터의 인상률·인센티브 수치는 모두 **임시값**입니다.

계산은 DB 함수 `fn_simulate_comp()` 한 곳에서만 수행됩니다. 세부 로직이 확정되면 이 함수와
정책 테이블(`comp_policy`, `comp_rule`, `salary_band`)만 수정하면 화면에 그대로 반영됩니다.
자세한 설계는 [docs/schema-design.md](docs/schema-design.md) 참고.

## 로그인 · 권한 (ID / 비밀번호)

| 역할 | 판정 기준 | 첫 화면 | 권한 |
|------|----------|--------|------|
| 부서장 | `department.head_employee_id` | 평가 입력 | 자기 부서 평가 입력 · 제출 |
| 인사팀 | `app_user_role` 의 `HR_ADMIN` | 부서별 현황 | 전 부서 현황 · 부서원 상세 조회 (조회 전용) |
| 그 외 | – | – | 접근 불가 |

- 로그인 ID는 **사번**, 비밀번호는 scrypt 해시로만 저장합니다 (원문 저장 안 함).
- 처음 로그인하거나 비밀번호가 초기화되면 **비밀번호 변경이 강제**됩니다.
  규칙: 8자 이상, 영문+숫자 포함, 사번 포함 불가.
- **5회 연속 실패 시 15분 잠금**. 없는 ID와 틀린 비밀번호는 같은 메시지로 응답합니다.
- 세션은 서명된 쿠키(HttpOnly, SameSite=Lax, 운영 시 Secure)로 8시간 유지되며,
  비밀번호를 바꾸면 다른 기기의 기존 세션은 모두 무효화됩니다.
- 평가 변경 이력(`evaluation_history.changed_by`)에 로그인 사용자 사번이 기록됩니다.

### 계정 관리 (관리자 명령)

```bash
python -m app.manage reset-password E002     # 임시 비밀번호 발급 (계정이 없으면 생성), 첫 로그인 시 변경
python -m app.manage unlock E002             # 잠금 해제
python -m app.manage grant-hr E007           # 인사팀 권한 부여 / revoke-hr 로 회수
python -m app.manage list                    # 계정 목록 (인사팀 여부, 변경 필요, 잠김, 최근 로그인)
```

### 더미 데이터 계정

본부 2개 · 팀/Lab 6개 · 부서별 30~40명(총 220여 명). 재무팀·AI Lab·Data Lab은 평가 제출 완료
(AI Lab은 상위평가율 40% 초과), Cloud Lab은 입력 중, 전략기획팀·인사팀은 미입력 상태입니다. 초기 비밀번호는 모두 `axmark2026` 입니다.

| 아이디 | 이름 | 역할 |
|--------|------|------|
| E002 | 김도윤 | 전략기획팀장 (미입력 – 입력 시연용) |
| E007 | 오세린 | 인사팀 담당자 (부서별 현황) |
| E006 | 한유진 | 인사팀장 + 인사팀 권한 (두 화면 모두) |
| E010 / E014 / E015 / E016 | 서지훈 / 문성호 / 배수아 / 노지환 | Cloud Lab / 재무팀 / AI Lab / Data Lab 부서장 |
| E001 / E009 | 정현우 / 강민석 | 경영지원본부장 / 개발본부장 (소속 팀장·Lab장 평가) |

> 더미 데이터의 초기 비밀번호는 시연용입니다. 운영 데이터 적재 시에는 `reset-password` 로 개별 발급하세요.

## 실행 방법

PostgreSQL 15 이상, Python 3.10 이상 필요.

```bash
pip install -r requirements.txt

createdb axmark
psql -d axmark -f db/schema.sql
psql -d axmark -f db/seed_sample.sql        # 샘플 데이터 (선택)

export DATABASE_URL=postgresql://<user>@localhost/axmark

# 로컬 실행 (http 이므로 Secure 쿠키 해제)
SESSION_HTTPS_ONLY=false uvicorn app.main:app --reload
# → http://localhost:8000  (E002 / axmark2026 으로 로그인)

# 운영 – .env.example 의 값을 환경변수로 설정
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

운영 시 반드시 HTTPS 리버스 프록시 뒤에서 실행하고 `SESSION_SECRET` 을 설정하세요.

## 테스트

```bash
pip install -r requirements-dev.txt
createdb axmark_test
TEST_DATABASE_URL=postgresql://<user>@localhost/axmark_test pytest
```

> 테스트는 `TEST_DATABASE_URL` DB의 public 스키마를 지우고 다시 만듭니다. 운영 DB를 지정하지 마세요.

## 구조

```
app/
  main.py          FastAPI – API 및 화면 제공
  auth.py          ID/비밀번호 로그인 · 비밀번호 변경 · 잠금 · 권한 판정
  manage.py        계정 관리 명령 (비밀번호 초기화, 잠금 해제, 인사팀 권한)
  db.py            DB 커넥션 풀
  static/          화면 – login / change-password / index(평가 입력) / hr(부서별 현황)
db/
  schema.sql       테이블 · 계산 함수 · 확정 처리
  seed_sample.sql  더미 데이터 (조직·220여 명·연봉·정책·진행 중 평가·계정)
docs/
  schema-design.md 설계 문서
tests/
  test_api.py      API 통합 테스트
```

## 미구현 / 다음 단계

- 인사팀 기능 확장: 등급별 인상률·인센티브 관리, 평가 조정(CALIBRATION), 확정(`fn_confirm_cycle`),
  화면에서의 계정 관리(현재는 명령어)
- 인사 마스터·연봉 데이터 연동 (현재는 SQL로 적재)
