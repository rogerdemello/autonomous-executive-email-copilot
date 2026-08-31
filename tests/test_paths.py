"""Filesystem anchors must survive being installed, not just run from a checkout.

Package assets and project data resolve differently, and getting them confused
is silent: templates that only resolve because the package directory happens to
share a name with a repo subdirectory will work in development and 500 in the
image. These tests pin which anchor each kind of path uses.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from app.core import paths


class TestPackageAssets:
    """Templates and static files ship inside the package."""

    def test_web_assets_live_under_the_package_not_the_repo_root(self):
        assert paths.WEB_DIR.parent == paths.PACKAGE_ROOT
        assert paths.PACKAGE_ROOT.name == "app"

    def test_every_template_the_ui_renders_exists(self):
        expected = {
            "base.html",
            "_public.html",
            "_app.html",
            "landing.html",
            "login.html",
            "signup.html",
            "connect.html",
            "inbox.html",
            "approvals.html",
            "activity.html",
            "settings.html",
        }
        present = {p.name for p in paths.TEMPLATES_DIR.glob("*.html")}
        assert expected <= present, f"missing templates: {expected - present}"

    def test_the_stylesheet_and_script_exist(self):
        assert (paths.STATIC_DIR / "app.css").is_file()
        assert (paths.STATIC_DIR / "app.js").is_file()

    def test_asset_paths_do_not_depend_on_the_repo_layout(self):
        """Resolved from the package, so a checkout and a wheel agree.

        The earlier form walked up to the repo root and back down through a
        hardcoded "app" segment, which only worked because those two names
        coincided.
        """
        relative = paths.TEMPLATES_DIR.relative_to(paths.PACKAGE_ROOT)
        assert relative == Path("web/templates")


class TestProjectData:
    """Configs and the demo mailbox live beside the package, not inside it."""

    def test_data_root_is_beside_the_package(self, monkeypatch):
        """With no override, project data resolves to <repo>/data.

        DATA_DIR has to be cleared explicitly: the test session always sets it
        (see tests/conftest.py) so the suite writes to a throwaway tree instead
        of the developer's real data/ directory. This asserts the *default*,
        which is what deployments without the override get.
        """
        monkeypatch.delenv("DATA_DIR", raising=False)
        reloaded = importlib.reload(paths)
        try:
            assert reloaded.DATA_ROOT.parent == reloaded.PROJECT_ROOT
            assert reloaded.DATA_ROOT.name == "data"
        finally:
            monkeypatch.undo()
            importlib.reload(paths)

    def test_the_files_the_app_actually_loads_are_present(self):
        assert (paths.DATA_ROOT / "settings.yaml").is_file()
        assert (paths.DATA_ROOT / "tasks.yaml").is_file()
        assert (paths.DEMO_DIR / "inbox.json").is_file()
        assert paths.SCENARIOS_DIR.is_dir()

    def test_data_dir_env_var_relocates_project_data(self, tmp_path, monkeypatch):
        """A deployment can mount data elsewhere without patching code."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        reloaded = importlib.reload(paths)
        try:
            assert reloaded.DATA_ROOT == tmp_path.resolve()
            assert reloaded.DEMO_DIR == tmp_path.resolve() / "demo"
            # Package assets are unaffected by the data override.
            assert reloaded.TEMPLATES_DIR.parent == reloaded.PACKAGE_ROOT / "web"
        finally:
            monkeypatch.delenv("DATA_DIR", raising=False)
            importlib.reload(paths)
