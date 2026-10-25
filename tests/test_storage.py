"""저장 계층: 로컬(내용 해시)·GitHub(파일 sha, PyGithub 대역)의 체크아웃·커밋·충돌 감지(폴더 구조)."""
import hashlib
import shutil
from types import SimpleNamespace

import pytest

from core.paths import DATA_DIR
from core.report import load_settings, save_settings
from core.storage import ConflictError, GitHubStorage, LocalStorage, storage_from_secrets


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    return d


def edit(ws, change):
    s = load_settings(ws.path)
    change(s)
    save_settings(s, ws.path)


def test_local_commit_and_conflict(data):
    st = LocalStorage(data)
    a, b = st.checkout(), st.checkout()
    assert "settings.json" in a.base
    edit(a, lambda s: setattr(s.계좌, "예금주", "홍길동"))
    assert st.commit(a, "설정: 계좌") == ["settings.json"]
    assert load_settings(data).계좌.예금주 == "홍길동"
    edit(b, lambda s: setattr(s.계좌, "예금주", "김철수"))
    with pytest.raises(ConflictError) as e:  # b는 a보다 먼저 받은 사본
        st.commit(b, "설정: 계좌")
    assert e.value.files == ["settings.json"]
    assert load_settings(data).계좌.예금주 == "홍길동"
    assert st.commit(a, "변경 없음") == []


def test_local_new_files(data):
    st = LocalStorage(data)
    ws = st.checkout()
    (ws.path / "2027").mkdir()
    (ws.path / "2027" / "memo.txt").write_text("x", encoding="utf-8")
    (ws.path / "첨부.bin").write_bytes(b"\x00\x01binary")
    assert sorted(st.commit(ws, "새 파일")) == ["2027/memo.txt", "첨부.bin"]
    assert (data / "2027" / "memo.txt").read_text(encoding="utf-8") == "x"


class NotFound(Exception):
    status = 404


class FakeRepo:
    """PyGithub Repository 대역: data/ 아래 파일(폴더 포함), git ref/blob/tree/commit."""
    full_name = "me/church-budget-agent"

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.commits = []
        self.blobs = {}

    @staticmethod
    def _sha(data):
        return hashlib.sha1(data).hexdigest()

    def _file(self, path):
        data = self.files[path]
        return SimpleNamespace(type="file", path=path, name=path.rsplit("/", 1)[-1], decoded_content=data,
                               content="x", sha=self._sha(data))

    def get_contents(self, path, ref=None):
        if path in self.files:
            return self._file(path)
        prefix = path.rstrip("/") + "/"
        children = {p[len(prefix):].split("/")[0] for p in self.files if p.startswith(prefix)}
        if not children:
            raise NotFound()
        out = []
        for c in sorted(children):
            full = prefix + c
            out.append(self._file(full) if full in self.files else SimpleNamespace(type="dir", path=full, name=c))
        return out

    def get_git_ref(self, name):
        return SimpleNamespace(object=SimpleNamespace(sha="head"), edit=lambda sha: None)

    def get_git_commit(self, sha):
        return SimpleNamespace(tree="tree0")

    def create_git_blob(self, content, encoding):
        import base64
        data = base64.b64decode(content)
        sha = self._sha(data)
        self.blobs[sha] = data
        return SimpleNamespace(sha=sha)

    def create_git_tree(self, elements, base):
        self._pending = elements
        return "tree1"

    def create_git_commit(self, message, tree, parents):
        items = [e._identity for e in self._pending]
        for it in items:
            self.files[it["path"]] = it["content"].encode("utf-8") if "content" in it else self.blobs[it["sha"]]
        self.commits.append((message, sorted(it["path"] for it in items)))
        return SimpleNamespace(sha="c1")


def repo_from(data):
    return FakeRepo({f"data/{p.relative_to(data).as_posix()}": p.read_bytes()
                     for p in data.rglob("*") if p.is_file()})


def test_github_commit_and_conflict(data):
    repo = repo_from(data)
    st = GitHubStorage(repo, "main")
    a, b = st.checkout(), st.checkout()
    assert "settings.json" in a.base
    edit(a, lambda s: setattr(s.연락처, "기안자", "010-0000-0001"))
    assert st.commit(a, "설정: 연락처") == ["settings.json"]
    assert repo.commits == [("설정: 연락처", ["data/settings.json"])]
    assert "010-0000-0001" in repo.files["data/settings.json"].decode("utf-8")
    edit(b, lambda s: setattr(s.연락처, "기안자", "x"))
    with pytest.raises(ConflictError):
        st.commit(b, "충돌")
    assert len(repo.commits) == 1


def test_github_binary_and_new_file(data):
    repo = repo_from(data)
    st = GitHubStorage(repo, "main")
    ws = st.checkout()
    (ws.path / "첨부.bin").write_bytes(b"\xff\xfe binary")
    assert st.commit(ws, "바이너리 저장") == ["첨부.bin"]
    assert repo.files["data/첨부.bin"] == b"\xff\xfe binary"


def test_storage_from_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("BUDGET_DATA_DIR", raising=False)
    s = storage_from_secrets({}, tmp_path)
    assert isinstance(s, LocalStorage) and s.data_dir == tmp_path
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "x"))
    assert storage_from_secrets({}, tmp_path).data_dir == tmp_path / "x"
