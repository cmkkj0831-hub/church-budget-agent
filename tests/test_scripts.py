"""배포 도우미 스크립트: 클라우드 점검(로컬 모드)."""
import shutil
import subprocess
import sys

from core.report import load_settings, save_settings
from core.paths import DATA_DIR, ROOT

PY = sys.executable


def test_check_cloud_local_mode_reads_only(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    s = load_settings(data)
    s.계좌.계좌번호, s.계좌.예금주, s.연락처.기안자 = "000-00-0000777", "홍길동", "010-0000-0777"
    save_settings(s, data)
    before = {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('ANTHROPIC_API_KEY = "sk-test-abcd"\nAPP_ENV = "dev"\n', encoding="utf-8")
    env = {"PATH": "", "BUDGET_DATA_DIR": str(data)}
    r = subprocess.run([PY, str(ROOT / "scripts" / "check_cloud.py"), "--secrets", str(secrets), "--skip-claude"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "비어 있는 칸: 기안단체장 연락처, 은행명" in r.stdout and "연락처·계좌 생성·읽기 일치" in r.stdout
    assert any(ln.startswith("보고서 왕복") and "| 통과" in ln for ln in r.stdout.splitlines())
    assert "0000777" not in r.stdout and "홍길동" not in r.stdout and "sk-test" not in r.stdout  # 값 출력 없음
    assert before == {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}  # 데이터는 그대로
