"""저장 계층: 로컬(내용 해시)·GitHub(파일 sha, PyGithub 대역)의 체크아웃·커밋·충돌 감지(폴더 구조)."""
import hashlib
import shutil
from types import SimpleNamespace

import pytest

from core.issues import IssueBook
from core.ledger import Ledger
from core.paths import DATA_DIR
from core.storage import ConflictError, GitHubStorage, LocalStorage, storage_from_secrets


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(DATA_DIR, d)
    return d


def edit(ws, change):
    L, B = Ledger.load(ws.path), IssueBook.load(ws.path)
    change(L, B)
    L.save(ws.path)
    B.save(ws.path)


def test_local_commit_and_conflict(data):
    st = LocalStorage(data)
    a, b = st.checkout(), st.checkout()
    assert "2026/ledger.csv" in a.base and "settings.json" in a.base
    edit(a, lambda L, B: setattr(B.get("I-019"), "상태", "확인중"))
    assert st.commit(a, "이슈: I-019 확인중") == ["2026/issues.csv"]
    assert IssueBook.load(data).get("I-019").상태 == "확인중"
    edit(b, lambda L, B: setattr(B.get("I-018"), "상태", "확인중"))
    with pytest.raises(ConflictError) as e:  # b는 a보다 먼저 받은 사본
        st.commit(b, "이슈: I-018 확인중")
    assert e.value.files == ["2026/issues.csv"]
    assert IssueBook.load(data).get("I-018").상태 == "미해결"
    assert st.commit(a, "변경 없음") == []


def test_local_new_files(data):
    st = LocalStorage(data)
    ws = st.checkout()
    (ws.path / "2027").mkdir()
    (ws.path / "2027" / "ledger.csv").write_text("x", encoding="utf-8")
    (ws.path / "settings_private.enc").write_bytes(b"\x00\x01binary")
    assert sorted(st.commit(ws, "새 연도")) == ["2027/ledger.csv", "settings_private.enc"]
    assert (data / "2027" / "ledger.csv").read_text(encoding="utf-8") == "x"


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
    assert set(a.base) >= {"2026/ledger.csv", "2026/issues.csv", "settings.json"}
    edit(a, lambda L, B: setattr(L.row(53), "행사명", "10월 교사회 운영비"))
    assert st.commit(a, "장부: (예)2026-052 행사명 정정") == ["2026/ledger.csv"]
    assert repo.commits == [("장부: (예)2026-052 행사명 정정", ["data/2026/ledger.csv"])]
    assert "10월 교사회 운영비" in repo.files["data/2026/ledger.csv"].decode("utf-8-sig")
    edit(b, lambda L, B: setattr(L.row(52), "행사명", "x"))
    with pytest.raises(ConflictError):
        st.commit(b, "충돌")
    assert len(repo.commits) == 1


def test_github_binary_and_new_file(data):
    repo = repo_from(data)
    st = GitHubStorage(repo, "main")
    ws = st.checkout()
    (ws.path / "settings_private.enc").write_bytes(b"\xff\xfe encrypted")
    assert st.commit(ws, "설정: 민감 설정 저장") == ["settings_private.enc"]
    assert repo.files["data/settings_private.enc"] == b"\xff\xfe encrypted"


def test_storage_from_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("BUDGET_DATA_DIR", raising=False)
    s = storage_from_secrets({}, tmp_path)
    assert isinstance(s, LocalStorage) and s.data_dir == tmp_path
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "x"))
    assert storage_from_secrets({}, tmp_path).data_dir == tmp_path / "x"
