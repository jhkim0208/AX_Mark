"""설정(.env) 읽기 – DB 없이 실행되는 단위 테스트"""
import os

from app import _load_dotenv


def test_dotenv_sets_defaults_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# 주석\n"
        "AXM_A=postgresql://u:p@localhost:5432/db\n"
        "AXM_B=false          # 줄 끝 주석\n"
        'AXM_C="quoted # value"\n'
        "AXM_EMPTY=\n"
        "AXM_KEEP=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AXM_KEEP", "from-env")
    for k in ("AXM_A", "AXM_B", "AXM_C", "AXM_EMPTY"):
        monkeypatch.delenv(k, raising=False)
    _load_dotenv(env)
    assert os.environ["AXM_A"] == "postgresql://u:p@localhost:5432/db"
    assert os.environ["AXM_B"] == "false"
    assert os.environ["AXM_C"] == "quoted # value"
    assert "AXM_EMPTY" not in os.environ
    assert os.environ["AXM_KEEP"] == "from-env"                     # 이미 설정된 환경변수가 우선
    for k in ("AXM_A", "AXM_B", "AXM_C"):
        monkeypatch.delenv(k)
