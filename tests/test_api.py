"""API 통합 테스트.

TEST_DATABASE_URL 이 가리키는 DB의 public 스키마를 초기화한 뒤
db/schema.sql + db/seed_sample.sql 을 적재하고 실행한다. (기존 데이터 삭제 주의)
"""
import dataclasses
import os
from pathlib import Path

import psycopg
import pytest

os.environ.setdefault("AUTH_MODE", "dev")      # SSO 없이 테스트 – 개발용 로그인 사용
TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL 미설정")

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute((ROOT / "db/schema.sql").read_text())
        conn.execute((ROOT / "db/seed_sample.sql").read_text())

    os.environ["DATABASE_URL"] = TEST_DB
    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

    db.DATABASE_URL = TEST_DB
    db.close_pool()
    with TestClient(app) as c:
        yield c


def login(client, emp_no):
    client.cookies.clear()
    res = client.get(f"/auth/dev-login?emp_no={emp_no}", follow_redirects=False)
    assert res.status_code == 303


@pytest.fixture()
def head(client):
    """전략기획팀(2) 부서장 김팀장으로 로그인"""
    login(client, "E002")
    return client


def put_grade(client, dept, emp, code):
    return client.put(f"/api/departments/{dept}/evaluations/{emp}", json={"grade_code": code})


def test_sheet_starts_blank_with_current_salary(head):
    sheet = head.get("/api/departments/2/sheet").json()
    names = [m["name"] for m in sheet["members"]]
    assert names == ["이과장", "박대리", "최사원"]           # 부서장(김팀장) 제외
    assert all(m["grade_code"] is None for m in sheet["members"])
    assert sheet["members"][0]["cur_base_salary"] == 72_000_000


def test_division_head_evaluates_team_heads(head):
    login(head, "E001")                                      # 경영지원본부장
    names = {m["name"] for m in head.get("/api/departments/1/sheet").json()["members"]}
    assert names == {"김팀장", "한팀장"}


def test_raise_by_individual_grade_only(head):
    # 이과장 72,000,000 / 당해 인센티브 5,000,000, 기본인상률 2% (임시값)
    a = put_grade(head, 2, 3, "A").json()
    assert (a["base_up_rate"], a["perf_raise_rate"], a["raise_rate"]) == (0.02, 0.05, 0.07)
    assert a["next_base_salary"] == 77_040_000
    assert a["next_incentive"] == 10_800_000                 # 72,000,000 × 15%
    assert a["delta_total"] == (77_040_000 + 10_800_000) - (72_000_000 + 5_000_000)

    # 부서가 달라도 같은 등급이면 같은 성과인상률
    login(head, "E010")                                      # 플랫폼팀장
    other = put_grade(head, 5, 11, "A").json()
    assert other["perf_raise_rate"] == a["perf_raise_rate"]


def test_d_freezes_and_e_is_negative(head):
    d = put_grade(head, 2, 4, "D").json()                  # 박대리 56,500,000
    assert d["perf_raise_rate"] == 0 and d["raise_rate"] == 0.02
    assert d["next_base_salary"] == 57_630_000

    e = put_grade(head, 2, 3, "E").json()                  # 이과장 72,000,000
    assert e["perf_raise_rate"] == -0.01 and e["raise_rate"] == 0.01
    assert e["next_base_salary"] == 72_720_000


def test_incentive_only_for_a_and_b(head):
    assert put_grade(head, 2, 3, "B").json()["next_incentive"] == 5_760_000   # 72,000,000 × 8%
    for code in ("C", "D", "E"):
        m = put_grade(head, 2, 3, code).json()
        assert m["next_incentive"] == 0
        assert m["delta_incentive"] == -5_000_000


@pytest.mark.parametrize(
    "code, perf, inc, message",
    [
        ("A", 0, 0.15, "양수"),
        ("D", 0.01, 0, r"0\(동결\)"),
        ("E", 0.01, 0, "음수"),
        ("C", 0.01, 0.05, "인센티브 지급 대상이 아닙니다"),
    ],
)
def test_rule_policy_is_enforced(head, code, perf, inc, message):
    with psycopg.connect(TEST_DB) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            conn.execute(
                """UPDATE comp_rule SET perf_raise_rate = %s, incentive_rate = %s
                    WHERE grade_id = (SELECT id FROM evaluation_grade WHERE code = %s)""",
                (perf, inc, code),
            )


def test_band_cap_and_clear_grade(head):
    m = put_grade(head, 2, 5, "A").json()                  # 사원 상한 5,200만
    assert m["next_base_salary"] == 52_000_000 and m["band_capped"] is True
    cleared = put_grade(head, 2, 5, None).json()
    assert cleared["grade_code"] is None and cleared["next_base_salary"] is None


def test_rejects_non_target_and_invalid_grade(head):
    assert put_grade(head, 2, 9, "A").status_code == 403   # 타 본부장
    assert put_grade(head, 2, 3, "S").status_code == 422


def test_submit_requires_all_and_locks(head):
    assert head.post("/api/departments/2/submit").status_code == 422
    for emp, g in [(3, "A"), (4, "C"), (5, "D")]:
        put_grade(head, 2, emp, g)
    assert head.post("/api/departments/2/submit").json() == {"submitted": 3}
    assert put_grade(head, 2, 3, "B").status_code == 409
    sheet = head.get("/api/departments/2/sheet").json()
    assert sheet["can_submit"] is False
    assert all(not m["editable"] for m in sheet["members"])


# ---------------------------------------------------------------------
# 로그인 / 권한
# ---------------------------------------------------------------------
def test_requires_login(client):
    assert client.get("/api/context").status_code == 401
    assert client.put("/api/departments/2/evaluations/3", json={"grade_code": "A"}).status_code == 401
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307 and res.headers["location"] == "/auth/login"


def test_non_manager_has_no_access(client):
    login(client, "E003")                                    # 이과장: 부서장 아님
    assert client.get("/api/context").status_code == 403


def test_head_sees_and_edits_only_own_department(head):
    depts = head.get("/api/context").json()["departments"]
    assert [(d["name"], d["editable"]) for d in depts] == [("전략기획팀", True)]
    assert head.get("/api/departments/3/sheet").status_code == 403      # 인사팀
    assert put_grade(head, 3, 7, "A").status_code == 403
    assert head.post("/api/departments/3/submit").status_code == 403


def test_hr_views_all_but_edits_only_own_team(client):
    login(client, "E006")                                    # 인사팀장 (HR_ADMIN)
    depts = client.get("/api/context").json()["departments"]
    assert len(depts) == 5
    sheet = client.get("/api/departments/2/sheet").json()
    assert sheet["read_only"] is True and sheet["can_submit"] is False
    assert all(not m["editable"] for m in sheet["members"])
    assert put_grade(client, 2, 3, "A").status_code == 403   # 타 부서는 조회 전용
    assert put_grade(client, 3, 7, "A").status_code == 200   # 자기 팀은 입력 가능


def test_history_records_logged_in_user(head):
    put_grade(head, 2, 3, "A")
    put_grade(head, 2, 3, "B")
    with psycopg.connect(TEST_DB) as conn:
        rows = conn.execute(
            """SELECT g.code, h.changed_by FROM evaluation_history h
                 LEFT JOIN evaluation_grade g ON g.id = h.new_grade_id ORDER BY h.id"""
        ).fetchall()
    assert rows == [("A", "E002"), ("B", "E002")]


def test_logout_clears_session(head):
    head.get("/auth/logout")
    assert head.get("/api/context").status_code == 401


def test_dev_login_disabled_in_oidc_mode(client, monkeypatch):
    from app import auth

    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, mode="oidc"))
    assert client.get("/auth/dev-login?emp_no=E002").status_code == 404


@pytest.mark.parametrize(
    "claims, status, logged_in",
    [
        ({"email": "E002@Example.com"}, 303, True),          # 대소문자 무시 매핑
        ({"email": "nobody@example.com"}, 403, False),       # 미등록 계정
        ({"sub": "abc"}, 401, False),                        # email 클레임 없음
    ],
)
def test_sso_callback_maps_email_to_employee(client, monkeypatch, claims, status, logged_in):
    from app import auth

    async def fake_userinfo(_request):
        return claims

    monkeypatch.setattr(auth, "settings", dataclasses.replace(auth.settings, mode="oidc"))
    monkeypatch.setattr(auth, "fetch_userinfo", fake_userinfo)
    res = client.get("/auth/callback", follow_redirects=False)
    assert res.status_code == status
    assert (client.get("/api/me").status_code == 200) is logged_in
