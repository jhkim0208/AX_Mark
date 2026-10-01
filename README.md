# AX Mark – 업적평가 · 보상 시뮬레이션

부서장이 부서원의 평가등급을 선택하면, 해당 부서의 **상위평가(조직평가)** 결과와 결합해
차년도 연봉·인센티브와 전년 대비 증감액을 즉시 보여주는 웹앱입니다.

## 화면 구성

| 영역 | 내용 |
|------|------|
| 상단 | 평가주기, 부서 선택, 평가자(부서장), 부서 상위평가 등급(상위 조직 결과 상속 여부 표시), 기본인상률 |
| 요약 | 입력 현황, 현재/예상 연봉 합계와 증감, 예상 인센티브 합계, 평균 인상률 |
| 표 | 사번 · 성명 · 직급 · 상위평가 · **현재 연봉** · **평가등급(선택박스, 기본 공란)** · 성과인상률 · 총인상률 · **차년도 연봉** · 연봉 증감 · **예상 인센티브** · 총보상 증감 |
| 하단 | 등급 분포 vs 배분 가이드(초과 시 강조), 평가 제출 |

- 등급을 선택하는 즉시 임시저장(DRAFT)되고 서버에서 재계산된 결과가 행에 반영됩니다.
- 제출하면 해당 부서 평가는 잠기며 수정할 수 없습니다.
- 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장 (예: 본부장 → 팀장 평가)

## 인상률 로직

```
총인상률   = 기본인상률(comp_policy.base_up_rate)
           + 성과인상률(comp_rule: 개인 평가등급 × 부서 상위평가 등급 매트릭스)
상위평가   = 자기 부서 평가 → 없으면 가장 가까운 상위 조직(본부 등)의 평가
차년 연봉  = 절사(현재 연봉 × (1 + 총인상률)) → 직급 연봉 밴드로 보정
인센티브   = 절사(기준연봉 × 인센티브율 × 전사 지급률 + 정액)
```

계산은 DB 함수 `fn_simulate_comp()` 한 곳에서만 수행됩니다. 세부 로직이 확정되면 이 함수와
정책 테이블(`comp_policy`, `comp_rule`, `salary_band`)만 수정하면 화면에 그대로 반영됩니다.
자세한 설계는 [docs/schema-design.md](docs/schema-design.md) 참고.

## 실행 방법

PostgreSQL 15 이상, Python 3.10 이상 필요.

```bash
pip install -r requirements.txt

createdb axmark
psql -d axmark -f db/schema.sql
psql -d axmark -f db/seed_sample.sql        # 샘플 데이터 (선택)

export DATABASE_URL=postgresql://<user>@localhost/axmark
uvicorn app.main:app --reload
# → http://localhost:8000
```

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

- **로그인·권한**: 현재는 부서를 선택하는 방식입니다. 운영 시 사내 SSO와 연동해 로그인한 부서장의
  부서만 조회·입력하도록 제한해야 합니다 (연봉 정보는 민감정보).
- 인사팀용 화면: 상위평가 입력, 정책 매트릭스 관리, 조정(CALIBRATION), 확정(`fn_confirm_cycle`)
- 인사 마스터·연봉 데이터 연동 (현재는 SQL로 적재)
