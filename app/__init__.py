"""프로젝트 루트의 .env 파일이 있으면 환경변수 기본값으로 읽어 둔다.
(이미 설정된 환경변수가 우선. 매번 터미널에서 export/set 하지 않아도 되도록)"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()   # 줄 끝 주석 제거
        if key and value:
            os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")
