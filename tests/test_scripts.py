"""배포 도우미 스크립트: 키 생성, 클라우드 점검(로컬 모드)."""
import shutil
import subprocess
import sys

from core.paths import DATA_DIR, ROOT
from core.private import load_private, new_key, save_private, PrivateSettings

PY = sys.executable


def test_gen_settings_key_prints_key_and_warning():
    out = subprocess.run([PY, str(ROOT / "scripts" / "gen_settings_key.py")], capture_output=True, text=True,
                         check=True).stdout
    assert out.startswith('SETTINGS_KEY = "') and "복구할 수 없습니다" in out


def test_check_cloud_local_mode_reads_only(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    key = new_key()
    p = PrivateSettings()
    p.계좌.계좌번호 = "000-00-0000777"
    save_private(p, key, data / "settings_private.enc")
    before = {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}
    secrets = tmp_path / "secrets.toml"
    secrets.write_text(f'ANTHROPIC_API_KEY = "sk-test-abcd"\nSETTINGS_KEY = "{key}"\n'
                       f'ADMIN_PASSWORD = "x"\nAPP_ENV = "dev"\n', encoding="utf-8")
    env = {"PATH": "", "BUDGET_DATA_DIR": str(data)}
    r = subprocess.run([PY, str(ROOT / "scripts" / "check_cloud.py"), "--secrets", str(secrets), "--skip-claude"],
                       capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "복호화 성공" in r.stdout and "계좌번호" in r.stdout and "hwpx 왕복" in r.stdout
    assert "0000777" not in r.stdout and key not in r.stdout and "sk-test" not in r.stdout  # 비밀값 출력 없음
    assert before == {f: f.read_bytes() for f in data.rglob("*") if f.is_file()}  # 데이터는 그대로
    assert load_private(key, data / "settings_private.enc").계좌.계좌번호 == "000-00-0000777"
