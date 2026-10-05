from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .auth import User, current_user, hr_user
from .db import close_pool, get_pool

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_pool()


app = FastAPI(title="업적평가 보상 시뮬레이션", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.settings.session_secret,
    session_cookie="axmark_session",
    max_age=auth.settings.session_max_age,
    same_site="lax",
    https_only=auth.settings.https_only,
)
app.include_router(auth.router)


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
           g.is_top_grade,
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


def _editable(cycle, member, user: User, dept_id: int) -> bool:
    return (user.can_edit(dept_id)
            and cycle["status"] == "OPEN"
            and member["evaluation_status"] in (None, "DRAFT"))


def _require_edit(user: User, dept_id: int) -> None:
    if not user.can_edit(dept_id):
        raise HTTPException(403, "이 부서의 평가를 입력할 권한이 없습니다.")


def _set_actor(cur, user: User) -> None:
    """평가 변경 이력(evaluation_history.changed_by)에 로그인 사용자 사번을 남긴다."""
    cur.execute("SELECT set_config('app.user', %s, true)", (user.emp_no,))


def _grades(cur, cycle_id: int):
    cur.execute(
        """SELECT g.code, g.name, g.raise_sign, g.incentive_eligible, g.is_top_grade,
                  gd.min_ratio, gd.max_ratio
             FROM evaluation_grade g
             LEFT JOIN grade_distribution_guide gd ON gd.grade_id = g.id
            WHERE g.cycle_id = %s ORDER BY g.sort_order""",
        (cycle_id,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------
def _page_user(request: Request) -> User | None:
    try:
        return auth.session_user(request)
    except HTTPException:
        return None


@app.get("/login")
def login_page(request: Request):
    user = _page_user(request)
    if user is not None:
        return RedirectResponse(auth.home_path(user))
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/change-password")
def change_password_page(request: Request):
    if _page_user(request) is None:
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "change-password.html")


@app.get("/")
def index(request: Request):
    """부서장 평가 입력 화면"""
    user = _page_user(request)
    if user is None or user.must_change_password or not user.head_of:
        return RedirectResponse("/login" if user is None else auth.home_path(user))
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/hr")
def hr_page(request: Request):
    """인사팀 부서별 현황 화면"""
    user = _page_user(request)
    if user is None or user.must_change_password or not user.is_hr:
        return RedirectResponse("/login" if user is None else auth.home_path(user))
    return FileResponse(STATIC_DIR / "hr.html")


# ---------------------------------------------------------------------
# API – 공통
# ---------------------------------------------------------------------
@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user.to_dict()


@app.get("/api/context")
def context(cycle_id: int | None = None, user: User = Depends(current_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        cur.execute("SELECT base_up_rate FROM comp_policy WHERE cycle_id = %s", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT d.id, d.name, d.parent_id, h.name AS head_name
                 FROM department d
                 LEFT JOIN employee h ON h.id = d.head_employee_id
                WHERE d.id = ANY(%s)
                ORDER BY d.code""",
            (user.head_of,),
        )
        departments = cur.fetchall()                    # 본인이 부서장인 부서만
    return {"cycle": cycle, "grades": grades, "policy": policy, "departments": departments,
            "user": user.to_dict()}


# ---------------------------------------------------------------------
# API – 부서장 평가 입력
# ---------------------------------------------------------------------
@app.get("/api/departments/{dept_id}/sheet")
def sheet(dept_id: int, cycle_id: int | None = None, user: User = Depends(current_user)):
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        dept = _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
    for m in members:
        m["editable"] = _editable(cycle, m, user, dept_id)
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
def save_grade(dept_id: int, employee_id: int, body: GradeInput,
               cycle_id: int | None = None, user: User = Depends(current_user)):
    """등급 선택 즉시 저장(DRAFT)하고 재계산된 처우를 돌려준다."""
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
        cycle = _cycle(cur, cycle_id)
        if cycle["status"] != "OPEN":
            raise HTTPException(409, "평가 입력 기간이 아닙니다.")
        _department(cur, dept_id)

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
            (cycle["id"], employee_id, user.employee_id, grade_id),
        )
        member = _members(cur, cycle, dept_id, employee_id)[0]
    member["editable"] = _editable(cycle, member, user, dept_id)
    return member


@app.post("/api/departments/{dept_id}/submit")
def submit(dept_id: int, cycle_id: int | None = None, user: User = Depends(current_user)):
    _require_edit(user, dept_id)
    with get_pool().connection() as conn, conn.cursor() as cur:
        _set_actor(cur, user)
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


# ---------------------------------------------------------------------
# API – 인사팀 현황
# ---------------------------------------------------------------------
def _stats(members: list[dict], grades: list[dict]) -> dict:
    """상위평가율 = 상위등급(is_top_grade) 인원 / 평가 입력 인원
       평균 성과인상률 = 평가 입력 인원의 성과인상률 단순 평균"""
    rated = [m for m in members if m["grade_code"] is not None]
    priced = [m for m in rated if m["perf_raise_rate"] is not None]
    submitted = [m for m in members if m["evaluation_status"] in ("SUBMITTED", "CONFIRMED")]
    n = len(rated)
    avg = lambda key: sum(m[key] for m in priced) / len(priced) if priced else None
    if not members:
        status = "NONE"
    elif len(submitted) == len(members):
        status = "SUBMITTED"
    elif n == 0:
        status = "NOT_STARTED"
    else:
        status = "IN_PROGRESS"
    return {
        "headcount": len(members),
        "rated": n,
        "submitted": len(submitted),
        "status": status,
        "grade_counts": {g["code"]: sum(1 for m in rated if m["grade_code"] == g["code"])
                         for g in grades},
        "top_rate": sum(1 for m in rated if m["is_top_grade"]) / n if n else None,
        "avg_perf_raise_rate": avg("perf_raise_rate"),
        "avg_raise_rate": avg("raise_rate"),
    }


@app.get("/api/hr/overview")
def hr_overview(cycle_id: int | None = None, user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        cur.execute("SELECT base_up_rate FROM comp_policy WHERE cycle_id = %s", (cycle["id"],))
        policy = cur.fetchone()
        cur.execute(
            """SELECT d.id, d.code, d.name, d.parent_id, p.name AS parent_name,
                      h.name AS head_name
                 FROM department d
                 LEFT JOIN department p ON p.id = d.parent_id
                 LEFT JOIN employee h ON h.id = d.head_employee_id
                ORDER BY COALESCE(d.parent_id, d.id), d.parent_id NULLS FIRST, d.code"""
        )
        departments = cur.fetchall()
        all_members = []
        for d in departments:
            members = _members(cur, cycle, d["id"])
            d.update(_stats(members, grades))
            all_members += members

    top_guide = [g["max_ratio"] for g in grades if g["is_top_grade"]]
    return {
        "cycle": cycle,
        "grades": grades,
        "policy": policy,
        "top_rate_guide": sum(top_guide) if top_guide and None not in top_guide else None,
        "total": _stats(all_members, grades),
        "departments": [d for d in departments if d["headcount"] > 0],
        "user": user.to_dict(),
    }


@app.get("/api/hr/departments/{dept_id}")
def hr_department(dept_id: int, cycle_id: int | None = None, user: User = Depends(hr_user)):
    with get_pool().connection() as conn, conn.cursor() as cur:
        cycle = _cycle(cur, cycle_id)
        grades = _grades(cur, cycle["id"])
        dept = _department(cur, dept_id)
        members = _members(cur, cycle, dept_id)
    return {"department": dept, "stats": _stats(members, grades), "members": members}
