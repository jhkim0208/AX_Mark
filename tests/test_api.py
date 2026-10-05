"""API 통합 테스트.

TEST_DATABASE_URL 이 가리키는 DB의 public 스키마를 초기화한 뒤
db/schema.sql + db/seed_sample.sql 을 적재하고 실행한다. (기존 데이터 삭제 주의)
"""
import os
from pathlib import Path

import psycopg
import pytest

os.environ.setdefault("SESSION_HTTPS_ONLY", "false")   # TestClient 는 http 로 접속
TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL 미설정")

ROOT = Path(__file__).resolve().parents[1]
INITIAL_PW = "axmark2026"


@pytest.fixture()
def client():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute((ROOT / "db/schema.sql").read_text())
        conn.execute((ROOT / "db/seed_sample.sql").read_text())
        # 비밀번호 변경 강제는 별도 테스트에서 확인 – 나머지 테스트는 바로 사용
        conn.execute("UPDATE user_account SET must_change_password = false")

    from fastapi.testclient import TestClient

    from app import db
    from app.main import app

    db.DATABASE_URL = TEST_DB
    db.close_pool()
    with TestClient(app) as c:
        yield c


def login(client, login_id, password=INITIAL_PW):
    client.cookies.clear()
    return client.post("/auth/login", json={"login_id": login_id, "password": password})


@pytest.fixture()
def head(client):
    """전략기획팀(2) 부서장 김도윤으로 로그인"""
    assert login(client, "E002").status_code == 200
    return client


def put_grade(client, dept, emp, code):
    return client.put(f"/api/departments/{dept}/evaluations/{emp}", json={"grade_code": code})


def db_rows(sql, *params):
    with psycopg.connect(TEST_DB) as conn:
        return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------
# 더미 데이터
# ---------------------------------------------------------------------
def test_dummy_data_has_30_to_40_members_per_team(client):
    rows = db_rows(
        """SELECT d.name, count(*) FROM employee e JOIN department d ON d.id = e.department_id
            WHERE d.parent_id IS NOT NULL AND e.id <> d.head_employee_id GROUP BY d.name"""
    )
    assert len(rows) == 6
    assert all(30 <= n <= 40 for _, n in rows)


# ---------------------------------------------------------------------
# 부서장 평가 입력 / 보상 계산
# ---------------------------------------------------------------------
def test_sheet_starts_blank_with_current_salary(head):
    sheet = head.get("/api/departments/2/sheet").json()
    members = {m["name"]: m for m in sheet["members"]}
    assert len(members) == 32 and "김도윤" not in members          # 부서장 제외
    assert all(m["grade_code"] is None for m in sheet["members"])
    assert members["이서준"]["cur_base_salary"] == 72_000_000
    assert {m["job_level"] for m in sheet["members"]} <= {"CL2", "CL3", "CL4"}


def test_division_head_evaluates_team_heads(client):
    login(client, "E001")                                            # 경영지원본부장
    names = {m["name"] for m in client.get("/api/departments/1/sheet").json()["members"]}
    assert names == {"김도윤", "한유진", "문성호"}


def test_raise_by_individual_grade_only(head):
    # 이서준 72,000,000 / 당해 인센티브 5,000,000, 기본인상률 2% (임시값)
    a = put_grade(head, 2, 3, "A").json()
    assert (a["base_up_rate"], a["perf_raise_rate"], a["raise_rate"]) == (0.02, 0.05, 0.07)
    assert a["next_base_salary"] == 77_040_000
    assert a["next_incentive"] == 10_800_000                         # 72,000,000 × 15%
    assert a["delta_total"] == (77_040_000 + 10_800_000) - (72_000_000 + 5_000_000)

    # 부서가 달라도 같은 등급이면 같은 성과인상률
    login(head, "E010")                                              # Cloud Lab장
    other = put_grade(head, 5, 11, "A").json()
    assert other["perf_raise_rate"] == a["perf_raise_rate"]


def test_d_freezes_and_e_is_negative(head):
    d = put_grade(head, 2, 4, "D").json()                            # 박지호 56,500,000
    assert d["perf_raise_rate"] == 0 and d["raise_rate"] == 0.02
    assert d["next_base_salary"] == 57_630_000

    e = put_grade(head, 2, 3, "E").json()                            # 이서준 72,000,000
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
def test_rule_policy_is_enforced(client, code, perf, inc, message):
    with psycopg.connect(TEST_DB) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match=message):
            conn.execute(
                """UPDATE comp_rule SET perf_raise_rate = %s, incentive_rate = %s
                    WHERE grade_id = (SELECT id FROM evaluation_grade WHERE code = %s)""",
                (perf, inc, code),
            )


def test_band_cap_and_clear_grade(head):
    # 박지호(CL2) 56,500,000 × 1.07 = 60,455,000 → CL2 상한 6,000만
    m = put_grade(head, 2, 4, "A").json()
    assert m["next_base_salary"] == 60_000_000 and m["band_capped"] is True
    cleared = put_grade(head, 2, 4, None).json()
    assert cleared["grade_code"] is None and cleared["next_base_salary"] is None


def test_rejects_non_target_and_invalid_grade(head):
    assert put_grade(head, 2, 9, "A").status_code == 403             # 타 본부장
    assert put_grade(head, 2, 3, "S").status_code == 422


def test_submit_requires_all_and_locks(head):
    assert head.post("/api/departments/2/submit").status_code == 422
    members = head.get("/api/departments/2/sheet").json()["members"]
    for m in members:
        assert put_grade(head, 2, m["employee_id"], "C").status_code == 200
    assert head.post("/api/departments/2/submit").json() == {"submitted": len(members)}
    assert put_grade(head, 2, 3, "B").status_code == 409
    sheet = head.get("/api/departments/2/sheet").json()
    assert sheet["can_submit"] is False
    assert all(not m["editable"] for m in sheet["members"])


def test_history_records_logged_in_user(head):
    put_grade(head, 2, 3, "A")
    put_grade(head, 2, 3, "B")
    rows = db_rows(
        """SELECT g.code, h.changed_by FROM evaluation_history h
             JOIN evaluation ev ON ev.id = h.evaluation_id
             LEFT JOIN evaluation_grade g ON g.id = h.new_grade_id
            WHERE ev.employee_id = 3 ORDER BY h.id"""
    )
    assert rows == [("A", "E002"), ("B", "E002")]


# ---------------------------------------------------------------------
# 로그인 (ID / 비밀번호)
# ---------------------------------------------------------------------
def test_requires_login(client):
    assert client.get("/api/context").status_code == 401
    assert put_grade(client, 2, 3, "A").status_code == 401
    for path in ("/", "/hr", "/change-password"):
        res = client.get(path, follow_redirects=False)
        assert res.status_code == 307 and res.headers["location"] == "/login"


def test_login_success_and_wrong_password(client):
    res = login(client, "e002")                                      # 아이디 대소문자 무시
    assert res.status_code == 200 and res.json()["redirect"] == "/"
    assert client.get("/api/me").json()["emp_no"] == "E002"

    wrong = login(client, "E002", "wrong-pass1")
    unknown = login(client, "E999", "whatever1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]        # 계정 존재 여부 노출 안 함
    assert client.get("/api/me").status_code == 401


def test_password_is_stored_hashed(client):
    (stored,) = db_rows("SELECT password_hash FROM user_account WHERE login_id = 'E002'")[0]
    assert stored.startswith("scrypt$") and INITIAL_PW not in stored


def test_lockout_after_five_failures(client):
    for _ in range(5):
        assert login(client, "E002", "wrong-pass1").status_code == 401
    assert login(client, "E002").status_code == 423                  # 맞는 비밀번호여도 잠김

    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("UPDATE user_account SET locked_until = NULL, failed_attempts = 0 "
                      "WHERE login_id = 'E002'")
    assert login(client, "E002").status_code == 200


def test_forced_password_change_on_first_login(client):
    from app.main import app
    from fastapi.testclient import TestClient

    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("UPDATE user_account SET must_change_password = true WHERE login_id = 'E002'")
    assert login(client, "E002").json()["redirect"] == "/change-password"
    res = client.get("/api/context")
    assert res.status_code == 403 and res.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"
    assert client.get("/", follow_redirects=False).headers["location"] == "/change-password"

    change = lambda cur, new: client.post(
        "/auth/change-password", json={"current_password": cur, "new_password": new})
    assert change("bad-current1", "NewPass2026").status_code == 400
    assert change(INITIAL_PW, "short1").status_code == 400            # 8자 미만
    assert change(INITIAL_PW, "onlyletters").status_code == 400       # 숫자 없음
    assert change(INITIAL_PW, "E002pass1234").status_code == 400      # 아이디 포함

    other = TestClient(app)                                          # 다른 기기의 기존 세션
    assert login(other, "E002").status_code == 200
    res = change(INITIAL_PW, "NewPass2026")
    assert res.status_code == 200 and res.json()["redirect"] == "/"
    assert client.get("/api/context").status_code == 200             # 현재 세션 유지
    assert other.get("/api/me").status_code == 401                   # 다른 세션은 무효화

    assert login(client, "E002").status_code == 401                  # 이전 비밀번호 불가
    assert login(client, "E002", "NewPass2026").status_code == 200


def test_reset_password_command(client):
    from app import manage

    temp = manage.reset_password("E003", None)                       # 계정 없던 이서준
    assert login(client, "E003", temp).json()["redirect"] == "/change-password"


def test_logout_clears_session(head):
    head.post("/auth/logout")
    assert head.get("/api/context").status_code == 401


# ---------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------
def test_account_without_role_is_denied(client):
    from app import manage

    pw = manage.reset_password("E003", "Member2026")
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("UPDATE user_account SET must_change_password = false WHERE login_id = 'E003'")
    assert login(client, "E003", pw).status_code == 200
    assert client.get("/api/context").status_code == 403


def test_head_sees_and_edits_only_own_department(head):
    depts = head.get("/api/context").json()["departments"]
    assert [d["name"] for d in depts] == ["전략기획팀"]
    assert head.get("/api/departments/3/sheet").status_code == 403    # 인사팀
    assert put_grade(head, 3, 7, "A").status_code == 403
    assert head.post("/api/departments/3/submit").status_code == 403
    assert head.get("/api/hr/overview").status_code == 403           # 인사팀 화면 불가
    assert head.get("/hr", follow_redirects=False).headers["location"] == "/"


def test_hr_staff_lands_on_hr_page(client):
    res = login(client, "E007")                                      # 인사 담당자 (부서장 아님)
    assert res.json()["redirect"] == "/hr"
    assert client.get("/", follow_redirects=False).headers["location"] == "/hr"
    assert client.get("/api/departments/2/sheet").status_code == 403  # 평가 입력 불가


# ---------------------------------------------------------------------
# 인사팀 부서별 현황
# ---------------------------------------------------------------------
def test_hr_overview_stats(client):
    login(client, "E007")
    data = client.get("/api/hr/overview").json()
    depts = {d["name"]: d for d in data["departments"]}
    assert len(depts) == 8

    assert {"Cloud Lab", "AI Lab", "Data Lab"} <= depts.keys()
    finance = depts["재무팀"]                                         # 시드: 전원 입력·제출
    assert (finance["headcount"], finance["rated"], finance["status"]) == (34, 34, "SUBMITTED")
    assert sum(finance["grade_counts"].values()) == 34

    rows = db_rows(
        """SELECT g.code, r.perf_raise_rate FROM evaluation ev
             JOIN employee e ON e.id = ev.employee_id
             JOIN evaluation_grade g ON g.id = ev.grade_id
             JOIN comp_rule r ON r.grade_id = g.id
            WHERE e.department_id = 6"""
    )
    top = sum(1 for code, _ in rows if code in ("A", "B"))
    assert finance["top_rate"] == pytest.approx(top / 34)
    assert finance["avg_perf_raise_rate"] == pytest.approx(float(sum(r for _, r in rows)) / 34)

    assert depts["전략기획팀"]["status"] == "NOT_STARTED"
    assert depts["Cloud Lab"]["status"] == "IN_PROGRESS"
    assert data["top_rate_limit"] == pytest.approx(0.40)             # 상위평가율 40% 초과 시 경고
    over = {name for name, d in depts.items() if d["top_rate"] is not None and d["top_rate"] > 0.40}
    assert over == {"AI Lab"}                                        # 시드에서 상위평가 과다 부서
    assert data["total"]["headcount"] == sum(d["headcount"] for d in depts.values())


def test_hr_overview_reflects_new_grades(client):
    login(client, "E002")
    put_grade(client, 2, 3, "A")
    put_grade(client, 2, 4, "C")
    login(client, "E006")                                            # 한유진 인사팀장 (부서장 겸 인사팀)
    d = next(d for d in client.get("/api/hr/overview").json()["departments"] if d["id"] == 2)
    assert (d["rated"], d["top_rate"], d["status"]) == (2, 0.5, "IN_PROGRESS")
    assert d["avg_perf_raise_rate"] == pytest.approx((0.05 + 0.015) / 2)


def test_hr_department_popup(client):
    login(client, "E007")
    res = client.get("/api/hr/departments/7").json()
    assert res["department"]["name"] == "AI Lab"
    assert len(res["members"]) == 40 == res["stats"]["headcount"]
    assert all(m["grade_code"] and m["perf_raise_rate"] is not None for m in res["members"])
