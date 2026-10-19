#
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

import pytest

from airflow.sdk import DAG, TaskGroup, task


def test_mapped_task_group_id_prefix_task_id():
    def f(z):
        pass

    with DAG(dag_id="d", schedule=None) as dag:
        x1 = dag.task(task_id="t1")(f).expand(z=[])
        with TaskGroup("g"):
            x2 = dag.task(task_id="t2")(f).expand(z=[])

    assert x1.operator.task_id == "t1"
    assert x2.operator.task_id == "g.t2"

    assert dag.get_task("t1") == x1.operator
    assert dag.get_task("g.t2") == x2.operator


@pytest.mark.parametrize(
    ("partial_kwargs", "expand_kwargs", "match"),
    [
        pytest.param({"x": "a"}, {"x": ["b"]}, "mapping already partial argument: x$", id="one"),
        pytest.param(
            {"x": "a", "y": "a"},
            {"x": ["b"], "y": ["b"]},
            "mapping already partial arguments: x, y$",
            id="many",
        ),
    ],
)
def test_expand_raises_on_argument_already_set_by_partial(partial_kwargs, expand_kwargs, match):
    @task
    def f(x, y=None):
        pass

    with DAG(dag_id="d", schedule=None):
        with pytest.raises(TypeError, match=match):
            f.partial(**partial_kwargs).expand(**expand_kwargs)
