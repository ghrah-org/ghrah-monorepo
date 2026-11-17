# SPDX-License-Identifier: Apache-2.0
"""代摘要、身份快照、占位符与启动策略的拒绝边界。"""

import copy
import json

import pytest
from ghrah_kernel.integration.assembly import host_configuration

from ghrah.subject.host.catalog import (
    CatalogError,
    load_catalog,
    map_host_configuration,
    map_supervisor,
)


def test_golden_catalog_maps_to_kernel_configuration(generation, catalog):
    assert load_catalog(generation) == catalog
    assert not (generation / "ghrah.toml").exists()
    data = map_host_configuration(catalog, {})
    launches, callers = host_configuration(data)
    assert launches.records[0].key == "kernel:namespace"
    assert json.loads(launches.records[0].configuration)["principal"] == '["local","ns"]'
    assert callers["control"].diagnostics
    specs, schemes, records = map_supervisor(catalog)
    assert specs[0].entry_key == records[0].key
    assert schemes["namespace"] == catalog["services"][0]["schemes"]


def test_expand_values_recursively_and_preserve_windows_paths(catalog):
    catalog["services"][0]["configuration"] = {
        "database": "${DATA_ROOT}/main.db",
        "nested": ["${DISTRO_ROOT}", {"id": "${GENERATION_ID}"}],
        "unchanged": "中文、反斜杠\\、普通字符串",
        "value": 1,
    }
    env = {
        "GHRAH_DATA_ROOT": r"C:\用户\data",
        "GHRAH_DISTRO_ROOT": r"C:\ghrah",
        "GHRAH_GENERATION_ID": "g1",
    }
    record = map_host_configuration(catalog, env)["launches"][0]
    configuration = json.loads(record["configuration"])
    assert configuration["database"] == r"C:\用户\data/main.db"
    assert configuration["nested"] == [r"C:\ghrah", {"id": "g1"}]
    assert configuration["unchanged"] == "中文、反斜杠\\、普通字符串"


@pytest.mark.parametrize("placeholder,env", [("${HOME}", {}), ("${DATA_ROOT}", {})])
def test_bad_placeholder_or_missing_trusted_environment_fails(catalog, placeholder, env):
    catalog["services"][0]["configuration"] = {"path": placeholder}
    with pytest.raises(CatalogError):
        map_host_configuration(catalog, env)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(schema=2),
        lambda c: c.update(schema=True),
        lambda c: c.update(services=[]),
        lambda c: c["callers"].pop("peer"),
        lambda c: c["callers"]["peer"].update(principal="unknown"),
        lambda c: c["services"].append(copy.deepcopy(c["services"][0])),
        lambda c: c["services"][0].update(restart="always"),
        lambda c: c["services"][0]["configuration"].update(principal="self-appointed"),
        lambda c: c["services"][0]["configuration"].update(data="x" * 65536),
    ],
)
def test_invalid_declarations_fail_before_spawn(catalog, mutation):
    mutation(catalog)
    with pytest.raises(CatalogError):
        map_host_configuration(catalog, {})


def test_same_kind_schemes_cannot_drift(catalog):
    service = copy.deepcopy(catalog["services"][0])
    service.update(id="other", kind="example")
    other = copy.deepcopy(service)
    other["id"] = "third"
    other["schemes"][0]["actions"].append("different")
    catalog["services"].extend((service, other))
    with pytest.raises(CatalogError, match="schemes"):
        map_supervisor(catalog)


def test_digest_compares_file_bytes_and_rejects_reformatting(generation):
    path = generation / "catalog.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(CatalogError, match="catalog_sha256"):
        load_catalog(generation)


def test_missing_generation_metadata_is_not_silently_accepted(generation):
    (generation / "GENERATION.toml").unlink()
    with pytest.raises(CatalogError):
        load_catalog(generation)
