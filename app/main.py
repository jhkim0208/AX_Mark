from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import close_pool, get_pool

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_pool()


app = FastAPI(title="업적평가 보상 시뮬레이션", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------
# 공통 조회
# ---------------------------------------------------------------------
def _cycle(cur, cycle_id: int | None):
    if cycle_id is None:
        cur.execute("SELECT * FROM evaluation_cycle ORDER BY eval_year DESC LIMIT 1")
    else:
        cur.execute("SELECT * FROM evaluation_cycle WHERE id = %s", (cycle_id,))
    cycle = cur.fetchone()
    if cycle is None:
        raise HTTPException(404, "평가주기가 없습니다.")
    return cycle


def _department(cur, department_id: int):
    cur.execute(
        """SELECT d.id, d.code, d.name, d.parent_id, d.head_employee_id,
                  h.name AS head_name
             FROM department d
             LEFT JOIN employee h ON h.id = d.head_employee_id
            WHERE d.id = %s""",
        (department_id,),
    )
    dept = cur.fetchone()
    if dept is None:
        raise HTTPException(404, "부서가 없습니다.")
    return dept


# 평가 대상: 부서원(부서장 제외) + 직속 하위 부서의 부서장
TARGETS_SQL = """
    SELECT e.id
      FROM employee e
      JOIN department d ON d.id = e.department_id
     WHERE e.department_id = %(dept)s
       AND e.id IS DISTINCT FROM d.head_employee_id
       AND e.status = 'ACTIVE'
    UNION
    SELECT c.head_employee_id
      FROM department c
     WHERE c.parent_id = %(dept)s
       AND c.head_employee_id IS NOT NULL
"""

MEMBERS_SQL = f"""
    WITH targets AS ({TARGETS_SQL})
    SELECT e.id              AS employee_id,
           e.emp_no,
           e.name,
           jl.name           AS job_level,
           md.name           AS department_name,
           ec.base_salary    AS cur_base_salary,
           ec.incentive_amount AS cur_incentive,
           g.code            AS grade_code,
           ev.status         AS evaluation_status,
           s.pay_grade_code, s.base_up_rate, s.perf_raise_rate, s.raise_rate,
           s.next_base_salary, s.next_incentive, s.next_total,
           s.delta_base_salary, s.delta_incentive, s.delta_total, s.band_capped
      FROM targets t
      JOIN employee e    ON e.id = t.id
      JOIN job_level jl  ON jl.id = e.job_level_id
      JOIN department md ON md.id = e.department_id
      LEFT JOIN employee_compensation ec
             ON ec.employee_id = e.id AND ec.comp_year = %(year)s
      LEFT JOIN evaluation ev
             ON ev.employee_id = e.id AND ev.cycle_id = %(cycle)s
      LEFT JOIN evaluation_grade g ON g.id = ev.grade_id
      LEFT JOIN LATERAL fn_simulate_comp(%(cycle)s, e.id, ev.grade_id) s ON true
    {{where}}
     ORDER BY jl.sort_order DESC, e.emp_no
"""


def _members(cur, cycle, dept_id: int, employee_id: int | None = None):
    where = "WHERE e.id = %(emp)s" if employee_id is not None else ""
    cur.execute(
        MEMBERS_SQL.format(where=where),
        {"dept": dept_id, "cycle": cycle["id"], "year": cycle["eval_year"], "emp": employee_id},
    )
    return cur.fetchall()


def _editable(cycle, member) -> bool:
    return cycle["status"] == "OPEN" and member["evaluation_status"] in (None, "DRAFT")


# ---------------------------------------------------------------------
# API
# ---------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/context")
def context(cycle_id: int | None = None):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        cur.execute(
            """SELECT g.code, g.name, g.raise_sign, g.incentive_eligible,
                      gd.min_ratio, gd.max_ratio
                 FROM evaluation_grade g
                 LEFT JOIN grade_distribution_guide gd ON gd.grade_id = g.id
                WHERE g.cycle_id = %s ORDER BY g.sort_order""",
            (cycle["id"],),
        )
        grades = cur.fetchall()
        cur.execute("SELECT base_up_rate FROM comp_policy WHERE cycle_id = %s", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT d.id, d.name, d.parent_id, h.name AS head_name
                 FROM department d
                 LEFT JOIN employee h ON h.id = d.head_employee_id
                ORDER BY d.code"""
        )
        departments = cur.fetchall()
    return {"cycle": cycle, "grades": grades, "policy": policy, "departments": departments}


@app.get("/api/departments/{dept_id}/sheet")
def sheet(dept_id: int, cycle_id: int | None = None):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        dept = _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
    for m in members:
        m["editable"] = _editable(cycle, m)
    return {
        "cycle": cycle,
        "department": dept,
        "members": members,
        "can_submit": cycle["status"] == "OPEN"
        and bool(members)
        and all(m["evaluation_status"] in (None, "DRAFT") for m in members),
    }


class GradeInput(BaseModel):
    grade_code: str | None = None


@app.put("/api/departments/{dept_id}/evaluations/{employee_id}")
def save_grade(dept_id: int, employee_id: int, body: GradeInput, cycle_id: int | None = None):
    """등급 선택 즉시 저장(DRAFT)하고 재계산된 처우를 돌려준다."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        if cycle["status"] != "OPEN":
            raise HTTPException(409, "평가 입력 기간이 아닙니다.")
        dept = _department(cur, dept_id)
        if dept["head_employee_id"] is None:
            raise HTTPException(409, "부서장이 지정되지 않은 부서입니다.")

        cur.execute(f"SELECT 1 FROM ({TARGETS_SQL}) t WHERE id = %(emp)s",
                    {"dept": dept_id, "emp": employee_id})
        if cur.fetchone() is None:
            raise HTTPException(403, "이 부서의 평가 대상자가 아닙니다.")

        grade_id = None
        if body.grade_code:
            cur.execute(
                "SELECT id FROM evaluation_grade WHERE cycle_id = %s AND code = %s",
                (cycle["id"], body.grade_code),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(422, "존재하지 않는 평가등급입니다.")
            grade_id = row["id"]

        cur.execute(
            "SELECT status FROM evaluation WHERE cycle_id = %s AND employee_id = %s FOR UPDATE",
            (cycle["id"], employee_id),
        )
        existing = cur.fetchone()
        if existing and existing["status"] != "DRAFT":
            raise HTTPException(409, "이미 제출된 평가는 수정할 수 없습니다.")

        cur.execute(
            """INSERT INTO evaluation (cycle_id, employee_id, evaluator_id, grade_id)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (cycle_id, employee_id)
               DO UPDATE SET grade_id = EXCLUDED.grade_id, evaluator_id = EXCLUDED.evaluator_id""",
            (cycle["id"], employee_id, dept["head_employee_id"], grade_id),
        )
        member = _members(cur, cycle, dept_id, employee_id)[0]
    member["editable"] = _editable(cycle, member)
    return member


@app.post("/api/departments/{dept_id}/submit")
def submit(dept_id: int, cycle_id: int | None = None):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        if cycle["status"] != "OPEN":
            raise HTTPException(409, "평가 입력 기간이 아닙니다.")
        _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
        missing = [m["name"] for m in members if m["grade_code"] is None]
        if missing:
            raise HTTPException(422, f"미입력 대상자가 있습니다: {', '.join(missing)}")
        cur.execute(
            f"""UPDATE evaluation SET status = 'SUBMITTED', submitted_at = now()
                 WHERE cycle_id = %(cycle)s AND status = 'DRAFT'
                   AND employee_id IN ({TARGETS_SQL})""",
            {"cycle": cycle["id"], "dept": dept_id},
        )
        count = cur.rowcount
    return {"submitted": count}
