# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from ci.prek import check_docs_redirects as hook

INITIAL_FILES = {
    "README.rst": "Readme\n",
    "script.py": "print('hello')\n",
    "docs/conf.py": "",
    "docs/index.rst": "Index\n",
    "docs/guide/setup.rst": "Setup\n=====\n\nHow to set things up.\n",
    "docs/_partials/note.rst": "Note\n",
}


def run_git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


@pytest.fixture
def docs_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    run_git("init", "--quiet")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")
    run_git("config", "commit.gpgsign", "false")
    for name, content in INITIAL_FILES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    run_git("add", "--all")
    run_git("commit", "--quiet", "--no-verify", "-m", "Initial commit")
    return tmp_path


def write_redirects(content: str) -> None:
    Path("docs/redirects.txt").write_text(content)
    run_git("add", "docs/redirects.txt")


class TestStagedChanges:
    def test_rename_without_redirect_fails(self, docs_repo):
        run_git("mv", "docs/guide/setup.rst", "docs/guide/installation.rst")

        assert hook.find_missing_redirects(hook.get_removed_rst_files()) == [
            hook.MissingRedirect(Path("docs/redirects.txt"), "guide/setup.rst guide/installation.rst")
        ]
        assert hook.main() == 1

    @pytest.mark.parametrize(
        "redirect_line",
        [
            "guide/setup.rst guide/installation.rst",
            "guide/setup.html guide/installation.html",
        ],
    )
    def test_rename_with_redirect_passes(self, docs_repo, redirect_line):
        run_git("mv", "docs/guide/setup.rst", "docs/guide/installation.rst")
        write_redirects(f"# Moved pages\n\n{redirect_line}\n")

        assert hook.main() == 0

    def test_delete_without_redirect_fails(self, docs_repo):
        run_git("rm", "--quiet", "docs/guide/setup.rst")

        assert hook.find_missing_redirects(hook.get_removed_rst_files()) == [
            hook.MissingRedirect(Path("docs/redirects.txt"), "guide/setup.rst <new-page>.rst")
        ]

    def test_delete_with_redirect_passes(self, docs_repo):
        run_git("rm", "--quiet", "docs/guide/setup.rst")
        write_redirects("guide/setup.rst index.rst\n")

        assert hook.main() == 0

    def test_move_out_of_docs_tree_suggests_placeholder(self, docs_repo):
        run_git("mv", "docs/guide/setup.rst", "setup.rst")

        assert hook.find_missing_redirects(hook.get_removed_rst_files()) == [
            hook.MissingRedirect(Path("docs/redirects.txt"), "guide/setup.rst <new-page>.rst")
        ]

    @pytest.mark.parametrize(
        "removed_file",
        [
            pytest.param("README.rst", id="rst-outside-docs-tree"),
            pytest.param("docs/_partials/note.rst", id="partial"),
            pytest.param("script.py", id="not-rst"),
        ],
    )
    def test_ignored_deletions_pass(self, docs_repo, removed_file):
        run_git("rm", "--quiet", removed_file)

        assert hook.main() == 0

    def test_removed_docs_tree_passes(self, docs_repo):
        run_git("rm", "--quiet", "-r", "docs")

        assert hook.main() == 0

    def test_modified_and_added_pages_pass(self, docs_repo):
        Path("docs/index.rst").write_text("Changed index\n")
        Path("docs/new.rst").write_text("New\n")
        run_git("add", "--all")

        assert hook.main() == 0


class TestRefRange:
    def test_uses_refs_from_prek_instead_of_staged_changes(self, docs_repo, monkeypatch):
        run_git("mv", "docs/guide/setup.rst", "docs/guide/installation.rst")
        run_git("commit", "--quiet", "--no-verify", "-m", "Rename page")
        monkeypatch.setenv("PRE_COMMIT_FROM_REF", "HEAD~1")
        monkeypatch.setenv("PRE_COMMIT_TO_REF", "HEAD")

        assert hook.get_removed_rst_files() == [
            hook.RemovedPage(Path("docs/guide/setup.rst"), Path("docs/guide/installation.rst"))
        ]
        assert hook.main() == 1

    def test_without_refs_committed_changes_are_not_checked(self, docs_repo):
        run_git("mv", "docs/guide/setup.rst", "docs/guide/installation.rst")
        run_git("commit", "--quiet", "--no-verify", "-m", "Rename page")

        assert hook.main() == 0
