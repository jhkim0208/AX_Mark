"""AX Mark 한 번에 실행 (start.bat 이 호출)

처음 실행하는 PC에서도 아래를 순서대로 알아서 처리한 뒤 서버를 켭니다.
  1. 필요한 파이썬 패키지 설치
  2. .env 설정 파일 만들기 (PostgreSQL 접속 정보를 물어봄)
  3. DB 만들기 · 테이블/더미 데이터 적재, DB 구조가 바뀌었으면 알려 줌
  4. Windows 방화벽에 포트 열기 (관리자 승인 창 1번)
  5. 이 PC의 접속 주소 안내 후 서버 실행
"""
import base64
import getpass
import hashlib
import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
SCHEMA = ROOT / "db" / "schema.sql"
SEED = ROOT / "db" / "seed_sample.sql"
IS_WINDOWS = os.name == "nt"
FIREWALL_RULE = "AX Mark (TCP {port})"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str, default: str = "") -> str:
    value = input(f"  {prompt}{f' [{default}]' if default else ''}: ").strip()
    return value or default


def yes(prompt: str, default: bool = True) -> bool:
    answer = input(f"  {prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
    return default if not answer else answer in ("y", "yes", "ㅛ", "예", "네")


def stop(msg: str) -> None:
    say(f"\n[!] {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------
# 1. 패키지
# ---------------------------------------------------------------------
def ensure_packages() -> None:
    needed = ("fastapi", "uvicorn", "psycopg", "psycopg_pool", "itsdangerous", "openpyxl")
    if all(importlib.util.find_spec(m) for m in needed):
        return
    say("[1/4] 필요한 프로그램(파이썬 패키지)을 설치합니다. 처음 한 번만 1~2분 걸립니다...")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")])
    if r.returncode != 0:
        stop("패키지 설치에 실패했습니다. 인터넷(사내 프록시) 연결을 확인하거나 IT 부서에 pip 사용을 문의하세요.")
    importlib.invalidate_caches()


# ---------------------------------------------------------------------
# 2. .env
# ---------------------------------------------------------------------
def read_env() -> dict:
    env = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    return env


def write_env(values: dict) -> None:
    lines = []
    if ENV_FILE.is_file():
        lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines()
    else:
        example = ROOT / ".env.example"
        lines = example.read_text(encoding="utf-8").splitlines() if example.is_file() else []
    for key, value in values.items():
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def connect(url: str, dbname: str | None = None):
    import psycopg

    if dbname:
        parts = urlsplit(url)
        url = parts._replace(path="/" + dbname).geturl()
    return psycopg.connect(url, autocommit=True, connect_timeout=5)


def ask_database_url(current: str | None) -> str:
    """PostgreSQL 접속 정보를 물어서 접속이 되는 URL 을 돌려준다."""
    import psycopg

    old = urlsplit(current) if current else None
    host, port = (old.hostname, str(old.port or 5432)) if old else ("localhost", "5432")
    user = unquote(old.username) if old and old.username else "postgres"
    dbname = old.path.lstrip("/") if old and old.path.lstrip("/") else "axmark"
    while True:
        say("\n  PostgreSQL 접속 정보를 입력하세요. ([ ] 안의 값은 엔터만 누르면 그대로 사용)")
        host = ask("PostgreSQL 주소", host)
        port = ask("포트", port)
        user = ask("사용자", user)
        password = getpass.getpass("  비밀번호 (입력해도 화면에 보이지 않습니다): ")
        dbname = ask("사용할 DB 이름", dbname)
        auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
        url = f"postgresql://{auth}@{host}:{port}/{dbname}"
        try:
            connect(url, "postgres").close()
            return url
        except psycopg.OperationalError as e:
            text = str(e)
            if "password" in text or "authentication" in text:
                say("  [!] 사용자 또는 비밀번호가 틀렸습니다.")
            elif "refused" in text or "timeout" in text or "could not" in text:
                say(f"  [!] {host}:{port} 에서 PostgreSQL 이 응답하지 않습니다.")
                say("      이 PC에 PostgreSQL 이 없다면 먼저 설치하세요: https://www.postgresql.org/download/windows/")
                say("      (설치할 때 정한 비밀번호와 포트 5432 를 기억해 두세요)")
            else:
                say(f"  [!] 접속 실패: {text.strip()}")
            if not yes("다시 입력할까요?"):
                stop("DB 접속 정보 없이 서버를 켤 수 없습니다.")


def ensure_env() -> dict:
    import psycopg

    env = read_env()
    url = env.get("DATABASE_URL")
    if url and "비밀번호" not in url:
        try:
            connect(url, "postgres").close()
        except psycopg.OperationalError:
            say(f"[!] .env 의 DB 접속 정보로 접속할 수 없습니다 ({urlsplit(url).hostname}:{urlsplit(url).port or 5432}).")
            url = None
    else:
        url = None
    if url is None:
        say("[2/4] 설정 파일(.env)을 만듭니다.")
        current = env.get("DATABASE_URL")
        url = ask_database_url(current if current and "비밀번호" not in current else None)
        write_env({"DATABASE_URL": url, "SESSION_HTTPS_ONLY": "false"})
        say("  → .env 저장 완료")
        env = read_env()
    elif env.get("SESSION_HTTPS_ONLY", "").lower() != "false":
        write_env({"SESSION_HTTPS_ONLY": "false"})   # http://IP 접속에서 로그인이 되도록
        env = read_env()
    return env


# ---------------------------------------------------------------------
# 3. DB
# ---------------------------------------------------------------------
def schema_hash() -> str:
    return hashlib.sha256(SCHEMA.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def load_schema(url: str) -> None:
    with connect(url) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.execute(SEED.read_text(encoding="utf-8"))
        conn.execute(f"COMMENT ON SCHEMA public IS 'axmark-schema {schema_hash()}'")


def ensure_database(url: str) -> None:
    from psycopg import sql

    dbname = urlsplit(url).path.lstrip("/")
    with connect(url, "postgres") as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            say(f"[3/4] DB '{dbname}' 를 만들고 기본 데이터를 넣습니다.")
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    with connect(url) as conn:
        has_tables = conn.execute("SELECT to_regclass('public.evaluation_cycle') IS NOT NULL").fetchone()[0]
        marker = conn.execute("SELECT obj_description('public'::regnamespace, 'pg_namespace')").fetchone()[0] or ""
        has_new = conn.execute("SELECT to_regclass('public.import_log') IS NOT NULL").fetchone()[0]

    if not has_tables:
        load_schema(url)
        say("  → 완료 (더미 데이터 포함. 인사팀 E006 / 부서장 E002, 초기 비밀번호는 README 참고)")
        return
    if marker == f"axmark-schema {schema_hash()}":
        return
    if not marker and has_new:
        # 예전에 직접 적재한 DB – 현재 구조로 보이므로 표시만 남긴다
        with connect(url) as conn:
            conn.execute(f"COMMENT ON SCHEMA public IS 'axmark-schema {schema_hash()}'")
        return
    say("\n[!] 프로그램이 업데이트되어 DB 구조가 바뀌었습니다. 이대로 켜면 일부 화면이 동작하지 않을 수 있습니다.")
    say("    DB를 새 구조로 다시 만들면 지금까지 입력·업로드한 데이터가 모두 지워지고 더미 데이터로 돌아갑니다.")
    if input("    다시 만들려면 '초기화' 라고 입력하세요 (그냥 엔터 = 그대로 진행): ").strip() == "초기화":
        load_schema(url)
        say("  → DB를 새 구조로 다시 만들었습니다.")
    else:
        say("  → 그대로 진행합니다.")


# ---------------------------------------------------------------------
# 4. 방화벽 (Windows)
# ---------------------------------------------------------------------
def ensure_firewall(port: int) -> None:
    if not IS_WINDOWS:
        return
    name = FIREWALL_RULE.format(port=port)
    r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                       capture_output=True)
    if r.returncode == 0:
        return
    say(f"[4/4] 다른 PC가 접속할 수 있도록 방화벽에서 {port}번 포트를 엽니다 (회사·개인 네트워크만).")
    say("      잠시 후 나오는 '이 앱이 디바이스를 변경하도록 허용하시겠어요?' 창에서 [예]를 눌러 주세요.")
    args = (f'advfirewall firewall add rule name="{name}" dir=in action=allow protocol=TCP '
            f'localport={port} profile=domain,private')
    ps = f"Start-Process -FilePath netsh -ArgumentList '{args}' -Verb RunAs -WindowStyle Hidden -Wait"
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded], capture_output=True)
    ok = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                        capture_output=True).returncode == 0
    say("  → 방화벽 열기 완료" if ok else
        "  [!] 방화벽을 열지 못했습니다. 이 PC에서는 쓸 수 있지만 다른 PC에서는 접속이 안 될 수 있습니다.\n"
        "      (관리자 권한이 없으면 IT 부서에 TCP 포트 허용을 요청하세요)")


# ---------------------------------------------------------------------
# 5. 실행
# ---------------------------------------------------------------------
def local_ips() -> list[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))      # 실제로 보내지 않음 – 기본 경로의 내 주소 확인용
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))         # app 패키지를 찾도록
    say("=" * 64)
    say("  AX Mark 업적평가 – 서버 시작")
    say("=" * 64)
    ensure_packages()
    env = ensure_env()
    ensure_database(env["DATABASE_URL"])
    port = int(env.get("PORT") or 8000)
    ensure_firewall(port)

    for k, v in env.items():          # .env 값을 서버 설정으로 사용
        if v:
            os.environ[k] = v
    say("\n" + "=" * 64)
    say(f"  이 PC에서      : http://localhost:{port}")
    for ip in local_ips():
        say(f"  다른 PC에서    : http://{ip}:{port}")
    say("  이 창을 닫거나 Ctrl+C 를 누르면 서버가 꺼집니다.")
    say("=" * 64 + "\n")

    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n서버를 껐습니다.")
