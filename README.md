# AX Mark – 업적평가 · 보상 시뮬레이션

부서장이 부서원의 개인 평가등급(A~E)을 선택하면 차년도 연봉·인센티브와
전년 대비 증감액을 즉시 보여주는 웹앱입니다.

## 화면 구성

| 영역 | 내용 |
|------|------|
| 상단 | 평가주기, 부서 선택, 평가자(부서장), 기본인상률, 인센티브 지급 등급 |
| 요약 | 입력 현황, 현재/예상 연봉 합계와 증감, 예상 인센티브 합계, 평균 인상률 |
| 표 | 사번 · 성명 · 직급 · **현재 연봉** · **평가등급(선택박스, 기본 공란)** · 성과인상률 · 총인상률 · **차년도 연봉** · 연봉 증감 · **예상 인센티브** · 총보상 증감 |
| 하단 | 등급 분포 vs 배분 가이드(초과 시 강조), 평가 제출 |

- 등급을 선택하는 즉시 임시저장(DRAFT)되고 서버에서 재계산된 결과가 행에 반영됩니다.
- 제출하면 해당 부서 평가는 잠기며 수정할 수 없습니다.
- 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장 (예: 본부장 → 팀장 평가)

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

## 로그인 · 권한 (사내 SSO)

표준 OpenID Connect(Authorization Code + PKCE)로 연동합니다. Entra ID(Azure AD), Google Workspace,
Okta, Keycloak 등 OIDC를 지원하는 사내 SSO라면 설정값만 바꿔서 사용할 수 있습니다.

| 역할 | 판정 기준 | 권한 |
|------|----------|------|
| 부서장 | `department.head_employee_id` | 자기 부서 조회 · 평가 입력 · 제출 |
| 인사팀 | `app_user_role` 의 `HR_ADMIN` | 전 부서 조회 (조회 전용) |
| 그 외 | – | 접근 불가 (403) |

- SSO 로그인 후 ID 토큰의 `email` 클레임을 `employee.email` 과 대소문자 무시로 매칭합니다.
  재직(ACTIVE) 직원이 아니면 접근이 거부되며, 요청마다 재직 여부를 다시 확인합니다.
- 세션은 서명된 쿠키(HttpOnly, SameSite=Lax, 운영 시 Secure)로 유지되고 기본 8시간 후 만료됩니다.
- 평가 변경 이력(`evaluation_history.changed_by`)에 로그인 사용자 사번이 기록됩니다.
- 로그아웃 시 IdP 가 `end_session_endpoint` 를 제공하면 SSO 세션까지 종료합니다.

### IdP 등록 시 필요한 정보

1. 앱 유형: Web application (confidential client)
2. Redirect URI: `https://<서비스 주소>/auth/callback`
3. 로그아웃 후 Redirect URI: `https://<서비스 주소>/auth/logged-out`
4. 발급받은 Issuer URL, Client ID, Client Secret 을 환경변수로 설정 (`.env.example` 참고)

## 실행 방법

PostgreSQL 15 이상, Python 3.10 이상 필요.

```bash
pip install -r requirements.txt

createdb axmark
psql -d axmark -f db/schema.sql
psql -d axmark -f db/seed_sample.sql        # 샘플 데이터 (선택)

export DATABASE_URL=postgresql://<user>@localhost/axmark

# 로컬 개발 (SSO 없이 사번 선택으로 로그인)
AUTH_MODE=dev uvicorn app.main:app --reload
# → http://localhost:8000  (샘플: E002 김팀장=부서장, E006 한팀장=부서장+인사팀)

# 운영 (사내 SSO) – .env.example 의 값을 환경변수로 설정
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

운영 시 반드시 HTTPS 리버스 프록시 뒤에서 실행하고 `AUTH_MODE=dev` 를 쓰지 마세요.

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
  auth.py          사내 SSO(OIDC) 로그인 · 세션 · 권한 판정
  db.py            DB 커넥션 풀
  static/          화면 (index.html, app.js, style.css)
db/
  schema.sql       테이블 · 계산 함수 · 확정 처리
  seed_sample.sql  샘플 조직/연봉/정책 매트릭스
docs/
  schema-design.md 설계 문서
tests/
  test_api.py      API 통합 테스트
```

## 미구현 / 다음 단계

- 실제 IdP 연동 테스트: 사내 SSO 앱 등록 후 Issuer/Client 정보로 확인 필요
- 인사팀용 화면: 등급별 인상률·인센티브 관리, 조정(CALIBRATION), 확정(`fn_confirm_cycle`)
- 인사 마스터·연봉 데이터 연동 (현재는 SQL로 적재)
