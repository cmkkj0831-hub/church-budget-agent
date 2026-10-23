"""장부 저장 계층. 앱은 세션마다 data/ 전체의 작업 사본(Workspace)을 받아 읽고 고친 뒤,
commit으로 바뀐(또는 새로 생긴) 파일만 되돌려 쓴다. 받은 뒤 원본이 바뀌었으면(다른 곳에서 먼저 저장) ConflictError.

data/ 구조: settings.json, {연도}/ledger.csv·plan.csv·plan_info.csv·issues.csv·
history.csv·usage.csv. 파일은 data/ 기준 상대 경로(예: "2026/ledger.csv")로 다룬다.

- LocalStorage: data/ 파일을 직접 읽고 쓴다(충돌 판정은 내용 해시).
- GitHubStorage: GitHub API(PyGithub)로 data/를 읽고, 바뀐 파일을 한 커밋으로 올린다(충돌 판정은 파일 sha).
UI·LLM 의존성 없음. 어떤 저장소를 쓸지는 앱이 secrets를 보고 고른다(GITHUB_TOKEN이 있으면 GitHub).
"""
import hashlib
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

IGNORED = (".DS_Store",)


class ConflictError(RuntimeError):
    """작업 사본을 받은 뒤 원본이 바뀜."""

    def __init__(self, files):
        super().__init__(f"다른 곳에서 먼저 수정됨: {', '.join(files)} — 새로 불러온 뒤 다시 해 주세요")
        self.files = list(files)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and p.name not in IGNORED)


@dataclass
class Workspace:
    path: Path
    base: dict = field(default_factory=dict)     # 상대 경로 → 받을 때의 버전(해시 또는 GitHub sha)
    content: dict = field(default_factory=dict)  # 상대 경로 → 받을 때의 내용 해시(바뀐 파일 판단용)

    def changed(self) -> list[str]:
        return [n for n in _files(self.path) if digest((self.path / n).read_bytes()) != self.content.get(n)]


class Storage:
    mode = "local"

    def checkout(self) -> Workspace:
        raise NotImplementedError

    def commit(self, ws: Workspace, message: str) -> list[str]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    @staticmethod
    def _workspace() -> Path:
        return Path(tempfile.mkdtemp(prefix="budget_ws_"))


class LocalStorage(Storage):
    mode = "local"

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)

    def describe(self) -> str:
        return f"로컬 파일({self.data_dir})"

    def checkout(self) -> Workspace:
        ws = Workspace(self._workspace())
        for name in _files(self.data_dir):
            dst = ws.path / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.data_dir / name, dst)
            ws.base[name] = ws.content[name] = digest(dst.read_bytes())
        return ws

    def commit(self, ws: Workspace, message: str) -> list[str]:
        changed = ws.changed()
        stale = []
        for n in changed:
            target = self.data_dir / n
            current = digest(target.read_bytes()) if target.exists() else None
            if current != ws.base.get(n):
                stale.append(n)
        if stale:
            raise ConflictError(stale)
        for n in changed:
            target = self.data_dir / n
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws.path / n, target)
            ws.base[n] = ws.content[n] = digest(target.read_bytes())
        return changed


class GitHubStorage(Storage):
    """repo: PyGithub Repository(또는 같은 메서드를 가진 대역). 파일은 {prefix}{상대 경로}."""
    mode = "github"

    def __init__(self, repo, branch: str = "main", prefix: str = "data/"):
        self.repo, self.branch, self.prefix = repo, branch, prefix

    def describe(self) -> str:
        return f"GitHub {getattr(self.repo, 'full_name', '')}@{self.branch}/{self.prefix}"

    def _walk(self, path: str):
        items = self.repo.get_contents(path.rstrip("/"), ref=self.branch)
        for it in items if isinstance(items, list) else [items]:
            if it.type == "dir":
                yield from self._walk(it.path)
            elif it.name not in IGNORED:
                yield it

    def _sha(self, name: str) -> str | None:
        try:
            return self.repo.get_contents(self.prefix + name, ref=self.branch).sha
        except Exception as e:
            if getattr(e, "status", None) == 404:
                return None
            raise

    def checkout(self) -> Workspace:
        ws = Workspace(self._workspace())
        for f in self._walk(self.prefix):
            name = f.path[len(self.prefix):]
            dst = ws.path / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = f.decoded_content if f.content is not None else \
                self.repo.get_contents(f.path, ref=self.branch).decoded_content
            dst.write_bytes(data)
            ws.base[name] = f.sha
            ws.content[name] = digest(data)
        return ws

    def commit(self, ws: Workspace, message: str) -> list[str]:
        from github import InputGitTreeElement

        changed = ws.changed()
        if not changed:
            return []
        stale = [n for n in changed if self._sha(n) != ws.base.get(n)]
        if stale:
            raise ConflictError(stale)
        ref = self.repo.get_git_ref(f"heads/{self.branch}")
        parent = self.repo.get_git_commit(ref.object.sha)
        elements = []
        for n in changed:
            data = (ws.path / n).read_bytes()
            try:
                elements.append(InputGitTreeElement(self.prefix + n, "100644", "blob", content=data.decode("utf-8")))
            except UnicodeDecodeError:  # 이미지 등 바이너리
                blob = self.repo.create_git_blob(__import__("base64").b64encode(data).decode(), "base64")
                elements.append(InputGitTreeElement(self.prefix + n, "100644", "blob", sha=blob.sha))
        tree = self.repo.create_git_tree(elements, parent.tree)
        new = self.repo.create_git_commit(message, tree, [parent])
        ref.edit(new.sha)  # fast-forward만(다른 커밋이 끼어들면 GitHub가 거부)
        for n in changed:
            ws.base[n] = self._sha(n)
            ws.content[n] = digest((ws.path / n).read_bytes())
        return changed


def storage_from_secrets(secrets, default_dir) -> Storage:
    """secrets에 GITHUB_TOKEN이 있으면 GitHub(GITHUB_REPO, DATA_BRANCH), 아니면 로컬(DATA_DIR 또는 data/)."""
    import os
    secrets = secrets or {}
    if secrets.get("GITHUB_TOKEN"):
        from github import Auth, Github
        repo = Github(auth=Auth.Token(secrets["GITHUB_TOKEN"])).get_repo(secrets["GITHUB_REPO"])
        return GitHubStorage(repo, secrets.get("DATA_BRANCH", "data"), secrets.get("GITHUB_DATA_PREFIX", "data/"))
    return LocalStorage(os.environ.get("BUDGET_DATA_DIR") or secrets.get("DATA_DIR") or default_dir)
