#!/usr/bin/env python
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
# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = [
#   "rich>=13.6.0",
# ]
# ///
"""
Fail when a documentation page is moved or deleted without a ``redirects.txt`` entry.

Every Sphinx docs tree (a directory with ``conf.py``) may have a ``redirects.txt`` next to
it, where each line maps an old page to its new location, both relative to the docs tree
(see ``devel-common/src/sphinx_exts/redirects.py``). Without an entry, external links to
the old page break once the docs are published.

prek never passes deleted files to hooks, so this hook asks git for renamed and deleted
``.rst`` files itself: between ``--from-ref`` and ``--to-ref`` when prek is given them
(as in CI), otherwise in the staged changes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from common_prek_utils import console

REDIRECTS_FILE_NAME = "redirects.txt"


class RemovedPage(NamedTuple):
    old_path: Path
    new_path: Path | None  # None when the page was deleted


class MissingRedirect(NamedTuple):
    redirects_file: Path
    suggested_line: str


def get_removed_rst_files() -> list[RemovedPage]:
    from_ref = os.environ.get("PRE_COMMIT_FROM_REF")
    to_ref = os.environ.get("PRE_COMMIT_TO_REF")
    diff_target = f"{from_ref}...{to_ref}" if from_ref and to_ref else "--cached"
    output = subprocess.run(
        ["git", "diff", "--name-status", "-z", "-M", "--diff-filter=DR", diff_target],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # -z output is "D\0<path>\0" for deletions and "R<score>\0<old>\0<new>\0" for renames
    fields = output.split("\0")
    removed_pages = []
    index = 0
    while index < len(fields) - 1:
        status = fields[index]
        if status.startswith("R"):
            old_path, new_path = Path(fields[index + 1]), Path(fields[index + 2])
            index += 3
        else:
            old_path, new_path = Path(fields[index + 1]), None
            index += 2
        if old_path.suffix == ".rst":
            removed_pages.append(RemovedPage(old_path, new_path))
    return removed_pages


def find_docs_root(path: Path) -> Path | None:
    for parent in path.parents:
        if (parent / "conf.py").is_file():
            return parent
    return None


def read_redirect_sources(redirects_file: Path) -> frozenset[str]:
    if not redirects_file.is_file():
        return frozenset()
    # Comment lines start with "#", so their first token never matches a page path
    return frozenset(line.split()[0] for line in redirects_file.read_text().splitlines() if line.strip())


def suggest_redirect_target(page: RemovedPage, docs_root: Path) -> str:
    if (
        page.new_path is not None
        and page.new_path.suffix == ".rst"
        and page.new_path.is_relative_to(docs_root)
    ):
        return page.new_path.relative_to(docs_root).as_posix()
    return "<new-page>.rst"


def find_missing_redirects(removed_pages: list[RemovedPage]) -> list[MissingRedirect]:
    missing_redirects = []
    for page in removed_pages:
        docs_root = find_docs_root(page.old_path)
        if docs_root is None:
            # Not part of a Sphinx docs tree, or the whole tree was removed
            continue
        relative_path = page.old_path.relative_to(docs_root)
        if any(part.startswith("_") for part in relative_path.parts):
            # Partials and generated API pages (_partials, _api, ...) are not published pages
            continue
        redirects_file = docs_root / REDIRECTS_FILE_NAME
        sources = read_redirect_sources(redirects_file)
        if {relative_path.as_posix(), relative_path.with_suffix(".html").as_posix()} & sources:
            continue
        missing_redirects.append(
            MissingRedirect(
                redirects_file,
                f"{relative_path.as_posix()} {suggest_redirect_target(page, docs_root)}",
            )
        )
    return missing_redirects


def main() -> int:
    missing_redirects = find_missing_redirects(get_removed_rst_files())
    if not missing_redirects:
        return 0
    console.print("[red]Documentation pages were moved or deleted without a redirect.[/]")
    for missing in missing_redirects:
        console.print(f"\nAdd to [magenta]{missing.redirects_file}[/]:\n    {missing.suggested_line}")
    console.print(
        "\n[yellow]Deleted pages should redirect to the closest remaining page, so that external "
        "links to the old URL keep working.[/]"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
