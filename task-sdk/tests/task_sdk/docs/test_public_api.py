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

import ast
import importlib
from pathlib import Path

# Public in the stub but deliberately kept out of runtime __all__ (also ignored by the tests below).
STUB_ONLY_EXPORTS = {"SecretCache"}


def test_airflow_sdk_exports_exist():
    """
    Ensure that all names declared in airflow.sdk.__all__ are present as attributes on the module.
    """
    sdk = importlib.import_module("airflow.sdk")
    # Provide literal attribute for testing since it's declared in __all__
    template_mod = importlib.import_module("airflow.sdk.definitions.template")
    setattr(sdk, "literal", getattr(template_mod, "literal"))
    public_names = getattr(sdk, "__all__", [])
    missing = [name for name in public_names if not hasattr(sdk, name)]
    assert not missing, f"Missing exports in airflow.sdk: {missing}"


def test_airflow_sdk_no_unexpected_exports():
    """
    Ensure that no unexpected public attributes are present in airflow.sdk besides those in __all__.
    """
    sdk = importlib.import_module("airflow.sdk")
    public = set(getattr(sdk, "__all__", []))
    actual = {name for name in dir(sdk) if not name.startswith("_")}
    ignore = {
        "__getattr__",
        "__lazy_imports",
        "TYPE_CHECKING",
        "SecretCache",
        "annotations",
        "api",
        "bases",
        "configuration",
        "coordinators",
        "crypto",
        "definitions",
        "exceptions",
        "execution_time",
        "importers",
        "io",
        "lineage",
        "listener",
        "log",
        "module_loading",
        "observability",
        "plugins_manager",
        "providers_manager_runtime",
        "secrets_masker",
        "serde",
        "timezone",
        "types",
        "state",
        "yaml",
    }
    unexpected = actual - public - ignore
    assert not unexpected, f"Unexpected exports in airflow.sdk: {sorted(unexpected)}"


def test_lazy_imports_match_public_api():
    """
    Ensure that the dynamic lazy-imports mapping matches the public names in __all__,
    except for the version string.
    """
    import airflow.sdk as sdk

    lazy = getattr(sdk, "__lazy_imports", {})
    expected = set(getattr(sdk, "__all__", [])) - {"__version__"}
    ignore = {"SecretCache"}
    actual = set(lazy.keys())
    missing = expected - actual
    extra = actual - expected - ignore
    assert not missing, f"__lazy_imports missing entries for: {sorted(missing)}"
    assert not extra, f"__lazy_imports has unexpected entries: {sorted(extra)}"


def _extract_stub_all(stub: ast.Module) -> set[str]:
    for node in stub.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError("airflow/sdk/__init__.pyi does not define __all__")


def _extract_stub_bound_names(stub: ast.Module) -> set[str]:
    bound: set[str] = set()
    for node in stub.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((alias.asname or alias.name).partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, ast.Assign):
            bound.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
    return bound


def test_stub_matches_runtime_public_api():
    """
    Ensure that airflow/sdk/__init__.pyi exports the same names as airflow.sdk.__all__.

    Type checkers read only the stub, so a name missing there is invisible to users' IDEs,
    and a name only there passes type checking but fails at import time.
    """
    sdk = importlib.import_module("airflow.sdk")
    stub = ast.parse(Path(sdk.__file__).with_suffix(".pyi").read_text())
    stub_all = _extract_stub_all(stub)
    runtime_all = set(sdk.__all__)

    missing_from_stub = runtime_all - stub_all
    missing_from_runtime = stub_all - runtime_all - STUB_ONLY_EXPORTS
    unbound_in_stub = stub_all - _extract_stub_bound_names(stub)
    assert not missing_from_stub, f"__init__.pyi __all__ is missing: {sorted(missing_from_stub)}"
    assert not missing_from_runtime, f"__init__.py __all__ is missing: {sorted(missing_from_runtime)}"
    assert not unbound_in_stub, f"__init__.pyi lists but never imports: {sorted(unbound_in_stub)}"
