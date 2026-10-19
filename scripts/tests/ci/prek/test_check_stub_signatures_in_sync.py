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

import textwrap
from pathlib import Path

import check_stub_signatures_in_sync as module
import pytest
from check_stub_signatures_in_sync import compare_decorator_stub, compare_paired_stub

SOURCE = """
from typing import overload

class Hook:
    def __init__(self, conn_id, *, timeout=None, **kwargs): ...
    def run(self, sql, parameters=None): ...
    def _private(self): ...

@overload
def fetch(x: int) -> int: ...
@overload
def fetch(x: str) -> str: ...
def fetch(x): ...
"""


def write_pair(tmp_path: Path, stub: str, source: str = SOURCE) -> Path:
    (tmp_path / "mod.py").write_text(textwrap.dedent(source))
    stub_path = tmp_path / "mod.pyi"
    stub_path.write_text(textwrap.dedent(stub))
    return stub_path


class TestComparePairedStub:
    def test_in_sync(self, tmp_path):
        stub = write_pair(tmp_path, SOURCE.replace("def fetch(x): ...", ""))
        assert compare_paired_stub(stub, set()) == []

    @pytest.mark.parametrize(
        ("stub", "expected"),
        [
            pytest.param(
                SOURCE.replace("    def run(self, sql, parameters=None): ...\n", ""),
                "Hook.run is in mod.py but missing from the stub",
                id="missing-name",
            ),
            pytest.param(
                SOURCE + "def extra() -> None: ...\n",
                "extra is in the stub but missing from mod.py",
                id="extra-name",
            ),
            pytest.param(
                SOURCE.replace("*, timeout=None", "*, timeout=None, retries=0"),
                "Hook.__init__ parameters differ",
                id="param-mismatch",
            ),
            pytest.param(
                SOURCE.replace("def run(self, sql,", "def run(self, query,"),
                "Hook.run parameters differ",
                id="param-renamed",
            ),
        ],
    )
    def test_reports_drift(self, tmp_path, stub, expected):
        errors = compare_paired_stub(write_pair(tmp_path, stub), set())
        assert len(errors) == 1
        assert expected in errors[0]

    def test_allowlist_is_honoured(self, tmp_path):
        stub = write_pair(tmp_path, SOURCE + "def extra() -> None: ...\n")
        assert compare_paired_stub(stub, {"extra"}) == []


OPERATORS = """
from airflow.sdk.bases.operator import BaseOperator
from airflow.reexport import Mixin

class DemoOperator(BaseOperator):
    def __init__(self, *, image, command, timeout=None, **kwargs): ...

class _DemoDecoratedOperator(Mixin, DemoOperator):
    def __init__(self, *, python_callable, task_id, op_args=None, serializer=None, **kwargs): ...
"""
REEXPORT = "from airflow.mixins import Mixin\n"
MIXINS = """
class Mixin:
    def __init__(self, *, retries_hint=None, **kwargs): ...
"""
STUB = """
class TaskDecoratorCollection:
    @overload
    def demo(self, *, multiple_outputs=None, serializer=None, image, timeout=None, retries_hint=None, **kwargs): ...
    @overload
    def demo(self, python_callable): ...
"""


@pytest.fixture
def decorator_tree(tmp_path, monkeypatch):
    src = tmp_path / "task-sdk" / "src" / "airflow"
    (src / "sdk" / "bases").mkdir(parents=True)
    (src / "sdk" / "bases" / "operator.py").write_text(
        "class BaseOperator:\n    def __init__(self, owner): ...\n"
    )
    (src / "demo_ops.py").write_text(textwrap.dedent(OPERATORS))
    (src / "reexport.py").write_text(REEXPORT)
    (src / "mixins.py").write_text(textwrap.dedent(MIXINS))
    monkeypatch.setattr(
        module, "DECORATED_OPERATORS", {"demo": ("airflow.demo_ops", "_DemoDecoratedOperator")}
    )
    monkeypatch.setattr(module, "DECORATOR_ALIASES", {})
    monkeypatch.setattr(module, "INTENTIONALLY_OMITTED", {"demo": {"command"}})
    monkeypatch.setattr(module, "KNOWN_MISSING_FROM_STUB", {})

    def write_stub(stub: str) -> Path:
        path = tmp_path / "decorators.pyi"
        path.write_text(textwrap.dedent(stub))
        return path

    return tmp_path, write_stub


class TestCompareDecoratorStub:
    def test_in_sync_following_bases_and_reexports(self, decorator_tree):
        root, write_stub = decorator_tree
        assert compare_decorator_stub(write_stub(STUB), root) == []

    def test_missing_operator_argument(self, decorator_tree):
        root, write_stub = decorator_tree
        errors = compare_decorator_stub(write_stub(STUB.replace(" timeout=None,", "")), root)
        assert errors == [f"{root / 'decorators.pyi'}: @task.demo is missing operator arguments ['timeout']"]

    def test_argument_the_operator_lacks(self, decorator_tree):
        root, write_stub = decorator_tree
        errors = compare_decorator_stub(write_stub(STUB.replace("image,", "image, use_dill=False,")), root)
        assert errors == [
            f"{root / 'decorators.pyi'}: @task.demo has arguments the operator lacks ['use_dill']"
        ]

    def test_known_missing_is_honoured(self, decorator_tree, monkeypatch):
        root, write_stub = decorator_tree
        monkeypatch.setattr(module, "KNOWN_MISSING_FROM_STUB", {"demo": {"timeout"}})
        assert compare_decorator_stub(write_stub(STUB.replace(" timeout=None,", "")), root) == []

    def test_stale_known_missing_is_reported(self, decorator_tree, monkeypatch):
        root, write_stub = decorator_tree
        monkeypatch.setattr(module, "KNOWN_MISSING_FROM_STUB", {"demo": {"timeout"}})
        errors = compare_decorator_stub(write_stub(STUB), root)
        assert errors == ["KNOWN_MISSING_FROM_STUB['demo'] has stale entries ['timeout']"]

    def test_unmapped_overload(self, decorator_tree):
        root, write_stub = decorator_tree
        stub = STUB + "    @overload\n    def other(self, *, x=None, **kwargs): ...\n"
        errors = compare_decorator_stub(write_stub(stub), root)
        assert len(errors) == 1
        assert "@task.other has no entry in DECORATED_OPERATORS" in errors[0]
