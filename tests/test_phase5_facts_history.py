import shutil
import subprocess
from pathlib import Path

import pytest

from ripple.facts import repository_facts
from ripple.history import HistoryError, co_changed
from ripple.scanner import scan_repository
from ripple.tools import ToolSession


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "RIPPLE Tests")
    _git(repo, "config", "user.email", "ripple@example.test")


@pytest.fixture
def soft_delete_repo(tmp_path: Path) -> Path:
    source = Path("tests/fixtures/soft_delete_app")
    shutil.copytree(source, tmp_path, dirs_exist_ok=True)
    _init(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    return tmp_path


def test_static_facts_cover_route_models_settings_and_migrations(
    soft_delete_repo: Path,
) -> None:
    index = scan_repository(soft_delete_repo)
    routes, _ = repository_facts(index, "routes")
    models, _ = repository_facts(index, "models")
    settings, _ = repository_facts(index, "settings")
    migrations, _ = repository_facts(index, "migrations")

    assert routes[0]["framework"] == "fastapi"
    assert routes[0]["methods"] == ["DELETE"]
    assert {item["framework"] for item in models} == {"pydantic", "sqlalchemy"}
    assert settings[0]["key"] == "DATABASE_URL"
    assert settings[0]["default"] == "sqlite:///app.db"
    assert migrations[0]["directory"] == "app/migrations"


def test_flask_and_django_routes_and_django_model(tmp_path: Path) -> None:
    (tmp_path / "views.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n"
        "@app.route('/health', methods=['GET', 'POST'])\n"
        "def health(): return 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom .views import health\n"
        "urlpatterns = [path('health/', health)]\n",
        encoding="utf-8",
    )
    (tmp_path / "models.py").write_text(
        "from django.db import models\n"
        "class Account(models.Model):\n    name = models.CharField(max_length=20)\n",
        encoding="utf-8",
    )
    _init(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    index = scan_repository(tmp_path)
    routes, _ = repository_facts(index, "routes")
    models, _ = repository_facts(index, "models")
    assert {item["framework"] for item in routes} == {"flask", "django"}
    assert models[0]["framework"] == "django"


def test_repo_facts_tool_errors_are_structured(indexed_repo) -> None:
    session = ToolSession(indexed_repo)
    unsupported = session.invoke("repo_facts", {"kind": "widgets"})
    empty = session.invoke("repo_facts", {"kind": "routes"})
    invalid = session.invoke("repo_facts", {"kind": "routes", "extra": True})
    assert unsupported.error.code == "unsupported_kind"
    assert empty.error.code == "none_detected"
    assert invalid.error.code == "invalid_arguments"


def test_cochange_counts_support_order_and_limit(tmp_path: Path) -> None:
    _init(tmp_path)
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "first")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py", "b.py")
    _git(tmp_path, "commit", "-qm", "second")
    result = co_changed(tmp_path, "a.py", limit=1)
    assert result.commits_with_path == 2
    assert result.partners[0].path == "b.py"
    assert result.partners[0].count == 2
    assert result.partners[0].support_ratio == 1.0
    assert result.truncated


def test_cochange_uses_head_history_only_and_excludes_future(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "past.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "future.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "future")
    _git(tmp_path, "branch", "future")
    _git(tmp_path, "checkout", "-q", "--detach", base)
    result = co_changed(tmp_path, "a.py")
    assert "future.py" not in {item.path for item in result.partners}


def test_cochange_unknown_and_no_partner_errors(tmp_path: Path) -> None:
    _init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "only")
    with pytest.raises(HistoryError, match="tracked path") as unknown:
        co_changed(tmp_path, "missing.py")
    assert unknown.value.code == "not_found"
    with pytest.raises(HistoryError, match="no co-change") as no_history:
        co_changed(tmp_path, "a.py")
    assert no_history.value.code == "no_history"
