"""계정 관리 명령 (인사팀/시스템 관리자용).

    python -m app.manage reset-password E002          # 임시 비밀번호 발급 (계정이 없으면 생성)
    python -m app.manage reset-password E002 --password 'Init1234'
    python -m app.manage unlock E002                  # 로그인 잠금 해제
    python -m app.manage grant-hr E007                # 인사팀 권한 부여
    python -m app.manage revoke-hr E007
    python -m app.manage list                         # 계정 목록
"""
import argparse
import secrets
import sys

from .auth import hash_password
from .db import get_pool


def _employee(cur, emp_no: str) -> dict:
    cur.execute("SELECT id, emp_no, name FROM employee WHERE emp_no = %s", (emp_no,))
    emp = cur.fetchone()
    if emp is None:
        sys.exit(f"사번 {emp_no} 직원이 없습니다.")
    return emp


def reset_password(emp_no: str, password: str | None) -> str:
    """임시 비밀번호로 설정한다. 다음 로그인 시 변경이 강제된다."""
    password = password or secrets.token_urlsafe(9)
    with get_pool().connection() as conn, conn.cursor() as cur:
        emp = _employee(cur, emp_no)
        cur.execute(
            """INSERT INTO user_account (employee_id, login_id, password_hash, must_change_password)
               VALUES (%s, %s, %s, true)
               ON CONFLICT (employee_id) DO UPDATE
                  SET password_hash = EXCLUDED.password_hash,
                      must_change_password = true,
                      failed_attempts = 0, locked_until = NULL, is_active = true,
                      password_changed_at = clock_timestamp()""",
            (emp["id"], emp["emp_no"], hash_password(password)),
        )
    return password


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("reset-password", help="임시 비밀번호 발급/계정 생성")
    p.add_argument("emp_no")
    p.add_argument("--password", help="지정하지 않으면 임의 생성")
    for name in ("unlock", "grant-hr", "revoke-hr"):
        sub.add_parser(name).add_argument("emp_no")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.cmd == "reset-password":
        pw = reset_password(args.emp_no, args.password)
        print(f"{args.emp_no} 임시 비밀번호: {pw}  (첫 로그인 시 변경 필요)")
        return

    with get_pool().connection() as conn, conn.cursor() as cur:
        if args.cmd == "list":
            cur.execute(
                """SELECT a.login_id, e.name, a.must_change_password, a.locked_until > now() AS locked,
                          a.last_login_at,
                          EXISTS (SELECT 1 FROM app_user_role r WHERE r.employee_id = e.id) AS hr
                     FROM user_account a JOIN employee e ON e.id = a.employee_id
                    ORDER BY a.login_id"""
            )
            for r in cur.fetchall():
                flags = [f for f, on in (("인사팀", r["hr"]), ("변경필요", r["must_change_password"]),
                                         ("잠김", r["locked"])) if on]
                print(f"{r['login_id']:8} {r['name']:6} {' '.join(flags):16} 최근로그인 {r['last_login_at'] or '-'}")
            return
        emp = _employee(cur, args.emp_no)
        if args.cmd == "unlock":
            cur.execute("UPDATE user_account SET failed_attempts = 0, locked_until = NULL "
                        "WHERE employee_id = %s", (emp["id"],))
        elif args.cmd == "grant-hr":
            cur.execute("INSERT INTO app_user_role (employee_id, role) VALUES (%s, 'HR_ADMIN') "
                        "ON CONFLICT DO NOTHING", (emp["id"],))
        elif args.cmd == "revoke-hr":
            cur.execute("DELETE FROM app_user_role WHERE employee_id = %s AND role = 'HR_ADMIN'",
                        (emp["id"],))
        print(f"{args.cmd} 완료: {emp['emp_no']} {emp['name']}")


if __name__ == "__main__":
    main()
