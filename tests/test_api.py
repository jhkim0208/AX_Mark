"""API 통합 테스트.

TEST_DATABASE_URL 이 가리키는 DB의 public 스키마를 초기화한 뒤
db/schema.sql + db/seed_sample.sql 을 적재하고 실행한다. (기존 데이터 삭제 주의)
"""
import os
from pathlib import Path

import psycopg
import pytest

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


def put_grade(client, dept, emp, code):
    return client.put(f"/api/departments/{dept}/evaluations/{emp}", json={"grade_code": code})


def test_sheet_starts_blank_with_current_salary(client):
    sheet = client.get("/api/departments/2/sheet").json()
    names = [m["name"] for m in sheet["members"]]
    assert names == ["이과장", "박대리", "최사원"]           # 부서장(김팀장) 제외
    assert all(m["grade_code"] is None for m in sheet["members"])
    assert sheet["members"][0]["cur_base_salary"] == 72_000_000
    assert sheet["org_grade"]["code"] == "A"
    assert sheet["org_grade"]["inherited"] is True           # 본부 결과 상속


def test_division_head_evaluates_team_heads(client):
    names = {m["name"] for m in client.get("/api/departments/1/sheet").json()["members"]}
    assert names == {"김팀장", "한팀장"}


def test_same_grade_differs_by_org_grade(client):
    # 개인 A등급, 상위평가 A(전략기획팀) / S(인사팀) / B(플랫폼팀)
    a = put_grade(client, 2, 3, "A").json()
    s = put_grade(client, 3, 7, "A").json()
    b = put_grade(client, 5, 11, "A").json()
    assert (a["org_grade_code"], a["perf_raise_rate"]) == ("A", 0.045)
    assert (s["org_grade_code"], s["perf_raise_rate"]) == ("S", 0.055)
    assert (b["org_grade_code"], b["perf_raise_rate"]) == ("B", 0.035)
    # 이과장: 72,000,000 × (1 + 2% + 4.5%) = 76,680,000
    assert a["raise_rate"] == 0.065
    assert a["next_base_salary"] == 76_680_000
    assert a["delta_base_salary"] == 4_680_000
    assert a["next_incentive"] == 8_640_000                  # 72,000,000 × 12%
    assert a["delta_total"] == (76_680_000 + 8_640_000) - (72_000_000 + 5_000_000)


def test_band_cap_and_clear_grade(client):
    m = put_grade(client, 2, 5, "S").json()                  # 사원 상한 5,200만
    assert m["next_base_salary"] == 52_000_000 and m["band_capped"] is True
    cleared = put_grade(client, 2, 5, None).json()
    assert cleared["grade_code"] is None and cleared["next_base_salary"] is None


def test_rejects_non_target_and_invalid_grade(client):
    assert put_grade(client, 2, 9, "S").status_code == 403   # 타 본부장
    assert put_grade(client, 2, 3, "X").status_code == 422


def test_submit_requires_all_and_locks(client):
    assert client.post("/api/departments/2/submit").status_code == 422
    for emp, g in [(3, "A"), (4, "B"), (5, "C")]:
        put_grade(client, 2, emp, g)
    assert client.post("/api/departments/2/submit").json() == {"submitted": 3}
    assert put_grade(client, 2, 3, "S").status_code == 409
    sheet = client.get("/api/departments/2/sheet").json()
    assert sheet["can_submit"] is False
    assert all(not m["editable"] for m in sheet["members"])
