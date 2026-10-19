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
# requires-python = ">=3.10"
# dependencies = [
#   "rich>=13.6.0",
# ]
# ///
"""
Check that hand-maintained ``.pyi`` stubs stay in sync with the code they describe.

Type checkers and IDEs read only the stub when one sits next to a module, so a stub that
drifts silently hides new parameters or advertises removed ones. Two kinds of stubs exist:

1. A stub with a sibling ``.py`` (e.g. the ``common.sql`` API stubs): its public functions,
   classes and methods must match the module's, with the same parameter names.
2. ``TaskDecoratorCollection`` in the Task SDK decorators stub: each ``@task.<name>`` overload
   must accept the same keyword arguments as the ``__init__`` chain of the decorated operator.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from common_prek_utils import AIRFLOW_ROOT_PATH, console

TASK_DECORATOR_STUB = Path("task-sdk/src/airflow/sdk/definitions/decorators/__init__.pyi")
# The airflow.sdk stub re-exports lazily imported names; test_public_api.py compares it with __all__.
NOT_PAIRED_STUBS = {TASK_DECORATOR_STUB, Path("task-sdk/src/airflow/sdk/__init__.pyi")}

# Public names of a paired stub that may legitimately differ from its .py, per stub path.
PAIRED_STUB_ALLOWED_DIFFERENCES: dict[str, set[str]] = {}

DECORATED_OPERATORS: dict[str, tuple[str, str]] = {
    "python": ("airflow.providers.standard.decorators.python", "_PythonDecoratedOperator"),
    "virtualenv": (
        "airflow.providers.standard.decorators.python_virtualenv",
        "_PythonVirtualenvDecoratedOperator",
    ),
    "external_python": (
        "airflow.providers.standard.decorators.external_python",
        "_PythonExternalDecoratedOperator",
    ),
    "branch": ("airflow.providers.standard.decorators.branch_python", "_BranchPythonDecoratedOperator"),
    "branch_virtualenv": (
        "airflow.providers.standard.decorators.branch_virtualenv",
        "_BranchPythonVirtualenvDecoratedOperator",
    ),
    "branch_external_python": (
        "airflow.providers.standard.decorators.branch_external_python",
        "_BranchExternalPythonDecoratedOperator",
    ),
    "short_circuit": (
        "airflow.providers.standard.decorators.short_circuit",
        "_ShortCircuitDecoratedOperator",
    ),
    "docker": ("airflow.providers.docker.decorators.docker", "_DockerDecoratedOperator"),
    "kubernetes": ("airflow.providers.cncf.kubernetes.decorators.kubernetes", "_KubernetesDecoratedOperator"),
    "kubernetes_cmd": (
        "airflow.providers.cncf.kubernetes.decorators.kubernetes_cmd",
        "_KubernetesCmdDecoratedOperator",
    ),
    "sensor": ("airflow.providers.standard.decorators.sensor", "DecoratedSensorOperator"),
    "pyspark": ("airflow.providers.apache.spark.decorators.pyspark", "_PySparkDecoratedOperator"),
    "bash": ("airflow.providers.standard.decorators.bash", "_BashDecoratedOperator"),
}
# The stub's ``__call__`` aliases ``python``.
DECORATOR_ALIASES = {"__call__": "python"}
# BaseOperator arguments reach every decorator through ``**kwargs``, so the walk stops there.
# LoggingMixin (a base of BaseOperator, reached again through SkipMixin) takes no operator arguments.
STOP_BASES = {"BaseOperator", "LoggingMixin"}
FILLED_BY_EVERY_DECORATOR = {"python_callable", "op_args", "op_kwargs", "task_id", "kwargs_to_upstream"}
HANDLED_BY_TASK_DECORATOR_FACTORY = {"multiple_outputs"}
# Operator arguments the decorated operator sets itself, or deprecated ones the stub should not advertise.
INTENTIONALLY_OMITTED: dict[str, set[str]] = {
    "docker": {"command", "retrieve_output", "retrieve_output_path"},
    "kubernetes": {"cmds", "is_delete_operator_pod"},
    "kubernetes_cmd": {"cmds", "arguments", "is_delete_operator_pod"},
    "bash": {"bash_command"},
}
# TODO(#36408): operator arguments not yet typed in the stub (they still pass through ``**kwargs``).
# Remove an entry once the stub gains the parameter; the check fails on stale entries.
KNOWN_MISSING_FROM_STUB: dict[str, set[str]] = {
    "python": {"templates_exts"},
    "virtualenv": {"expect_airflow", "index_urls_from_connection_ids", "string_args", "templates_exts"},
    "external_python": {
        "expect_airflow",
        "expect_pendulum",
        "skip_on_exit_code",
        "string_args",
        "templates_exts",
    },
    "branch": {"show_return_value_in_logs", "templates_dict", "templates_exts"},
    "branch_virtualenv": {
        "env_vars",
        "expect_airflow",
        "index_urls_from_connection_ids",
        "inherit_env",
        "string_args",
        "templates_exts",
    },
    "branch_external_python": {
        "env_vars",
        "expect_airflow",
        "expect_pendulum",
        "inherit_env",
        "skip_on_exit_code",
        "string_args",
        "templates_exts",
    },
    "short_circuit": {"show_return_value_in_logs", "templates_dict", "templates_exts"},
    "docker": {"expect_airflow"},
    "kubernetes": {
        "automount_service_account_token",
        "callbacks",
        "container_name_log_prefix_enabled",
        "durable",
        "init_container_logs",
        "log_formatter",
        "logging_interval",
        "on_kill_action",
        "runtime_class_name",
        "schedule_timeout_seconds",
        "trigger_kwargs",
    },
    "sensor": {"never_fail", "silent_fail", "templates_dict"},
    "pyspark": {"show_return_value_in_logs", "templates_dict", "templates_exts"},
    "bash": {"output_processor"},
}
KNOWN_MISSING_FROM_STUB["kubernetes_cmd"] = KNOWN_MISSING_FROM_STUB["kubernetes"]


def format_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    args = fn.args
    names = [a.arg for a in (*args.posonlyargs, *args.args)]
    if args.vararg:
        names.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        names.append("*")
    names.extend(a.arg for a in args.kwonlyargs)
    if args.kwarg:
        names.append(f"**{args.kwarg.arg}")
    return tuple(names)


def is_overload(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "overload")
        or (isinstance(d, ast.Attribute) and d.attr == "overload")
        for d in fn.decorator_list
    )


def iter_definitions(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the statements of a body, descending into ``if`` blocks such as ``if TYPE_CHECKING``."""
    for node in body:
        if isinstance(node, ast.If):
            yield from iter_definitions(node.body)
            yield from iter_definitions(node.orelse)
        else:
            yield node


def extract_public_api(tree: ast.Module) -> dict[str, tuple[str, ...] | None]:
    """Map each public function, class and method to its parameters (``None`` when overloaded)."""
    api: dict[str, tuple[str, ...] | None] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in iter_definitions(body):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_") and node.name != "__init__":
                    continue
                name = prefix + node.name
                api[name] = None if is_overload(node) or name in api else format_params(node)
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                api[prefix + node.name] = ()
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body, "")
    return api


def compare_paired_stub(stub_path: Path, allowed: set[str]) -> list[str]:
    source_path = stub_path.with_suffix(".py")
    stub_api = extract_public_api(ast.parse(stub_path.read_text()))
    source_api = extract_public_api(ast.parse(source_path.read_text()))
    errors = []
    for name in sorted((source_api.keys() | stub_api.keys()) - allowed):
        if name not in stub_api:
            errors.append(f"{stub_path}: {name} is in {source_path.name} but missing from the stub")
        elif name not in source_api:
            errors.append(f"{stub_path}: {name} is in the stub but missing from {source_path.name}")
        elif None not in (stub_api[name], source_api[name]) and stub_api[name] != source_api[name]:
            errors.append(
                f"{stub_path}: {name} parameters differ: "
                f"{source_path.name} has {source_api[name]}, stub has {stub_api[name]}"
            )
    return errors


def find_module_file(module: str, root: Path) -> Path | None:
    parts = module.split(".")
    candidates = [root / "task-sdk" / "src", root / "airflow-core" / "src"]
    if parts[:2] == ["airflow", "providers"]:
        candidates += [root / "providers" / Path(*parts[2 : 2 + depth]) / "src" for depth in (1, 2, 3)]
    for src in candidates:
        for path in (src / Path(*parts)).with_suffix(".py"), src / Path(*parts) / "__init__.py":
            if path.is_file():
                return path
    return None


def find_reexport_sources(tree: ast.Module, name: str) -> Iterator[tuple[str, str]]:
    """Yield the ``(module, name)`` pairs a module may re-export ``name`` from."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    yield node.module, alias.name
        # Lazy re-exports of the compat provider: ``_IMPORT_MAP = {"Name": "module" | ("module", ...)}``.
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_IMPORT_MAP"
            and isinstance(node.value, ast.Dict)
        ):
            source = ast.literal_eval(node.value).get(name)
            for source_module in (source,) if isinstance(source, str) else source or ():
                yield source_module, name


def find_class(module: str, name: str, root: Path) -> tuple[str, ast.ClassDef] | None:
    """Find a class definition, following re-exports. Returns ``None`` for classes outside the repo."""
    path = find_module_file(module, root)
    if path is None:
        return None
    tree = ast.parse(path.read_text())
    for node in iter_definitions(tree.body):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return module, node
    for source_module, source_name in find_reexport_sources(tree, name):
        if source_module == module:
            continue
        try:
            found = find_class(source_module, source_name, root)
        except LookupError:
            continue
        if found:
            return found
    raise LookupError(f"Cannot find class {name} in {path}")


def collect_operator_params(module: str, class_name: str, root: Path) -> set[str]:
    """Collect the named ``__init__`` arguments along a class's bases, up to ``BaseOperator``."""
    found = find_class(module, class_name, root)
    if found is None:
        return set()
    module, cls = found
    params: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            args = node.args
            params.update(a.arg for a in (*args.posonlyargs, *args.args[1:], *args.kwonlyargs))
    for base in cls.bases:
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
        if base_name and base_name not in STOP_BASES:
            params |= collect_operator_params(module, base_name, root)
    return params


def extract_decorator_overloads(tree: ast.Module) -> dict[str, set[str]]:
    """Map each ``TaskDecoratorCollection`` method to the keyword-only arguments of its first overload."""
    collection = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TaskDecoratorCollection"
    )
    overloads: dict[str, set[str]] = {}
    for node in collection.body:
        if isinstance(node, ast.FunctionDef) and node.args.kwonlyargs and node.name not in overloads:
            overloads[node.name] = {a.arg for a in node.args.kwonlyargs}
    return overloads


def compare_decorator_stub(stub_path: Path, root: Path) -> list[str]:
    overloads = extract_decorator_overloads(ast.parse(stub_path.read_text()))
    errors = []
    for alias, target in DECORATOR_ALIASES.items():
        if overloads.get(alias) != overloads.get(target):
            errors.append(f"{stub_path}: {alias} must accept the same arguments as {target}")
    for name in sorted(overloads.keys() - DECORATED_OPERATORS.keys() - DECORATOR_ALIASES.keys()):
        errors.append(
            f"{stub_path}: @task.{name} has no entry in DECORATED_OPERATORS of {Path(__file__).name}"
        )
    for decorator, (module, class_name) in sorted(DECORATED_OPERATORS.items()):
        if decorator not in overloads:
            errors.append(f"{stub_path}: @task.{decorator} has no overload with keyword arguments")
            continue
        stub_params = overloads[decorator]
        operator_params = collect_operator_params(module, class_name, root)
        omitted = FILLED_BY_EVERY_DECORATOR | INTENTIONALLY_OMITTED.get(decorator, set())
        known_missing = KNOWN_MISSING_FROM_STUB.get(decorator, set())
        missing = operator_params - stub_params - omitted
        if new_missing := missing - known_missing:
            errors.append(
                f"{stub_path}: @task.{decorator} is missing operator arguments {sorted(new_missing)}"
            )
        if extra := stub_params - operator_params - HANDLED_BY_TASK_DECORATOR_FACTORY:
            errors.append(f"{stub_path}: @task.{decorator} has arguments the operator lacks {sorted(extra)}")
        if stale := known_missing - missing:
            errors.append(f"KNOWN_MISSING_FROM_STUB[{decorator!r}] has stale entries {sorted(stale)}")
    return errors


def find_stubs(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--", "*.pyi"], cwd=root, capture_output=True, text=True, check=True
    )
    return [Path(line) for line in result.stdout.splitlines() if "/tests/" not in line]


def main() -> int:
    os.chdir(AIRFLOW_ROOT_PATH)
    errors = compare_decorator_stub(TASK_DECORATOR_STUB, AIRFLOW_ROOT_PATH)
    for stub in find_stubs(AIRFLOW_ROOT_PATH):
        if stub in NOT_PAIRED_STUBS or not stub.with_suffix(".py").is_file():
            continue
        allowed = PAIRED_STUB_ALLOWED_DIFFERENCES.get(stub.as_posix(), set())
        errors += compare_paired_stub(stub, allowed)
    for error in errors:
        console.print(f"[red]{error}")
    if errors:
        console.print(
            "[yellow]Update the .pyi stub to match the code. If a difference is intentional, "
            f"allowlist it in {Path(__file__).relative_to(AIRFLOW_ROOT_PATH)}."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
