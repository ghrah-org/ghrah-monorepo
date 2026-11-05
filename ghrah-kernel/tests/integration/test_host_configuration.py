"""宿主身份目录是调用连接与服务派生快照的共同权威。"""

import json
from dataclasses import asdict

import pytest
from _runtime_host import A1, SVC, U1, U2, identities

from ghrah_kernel.integration.assembly import host_configuration
from ghrah_kernel.launch import LaunchRecord


def configuration():
    directory = identities()
    record = LaunchRecord(
        "names",
        "namespace",
        "ghrah_kernel.integration.entry",
        directory.bind(SVC.ref, None),
        configuration=json.dumps({"provider": "namespace", "administrator": U1.ref}),
    )
    return {
        "identities": directory.to_dict(),
        "launches": [asdict(record)],
        "callers": {
            name: asdict(directory.bind(principal, scope))
            for name, principal, scope in (
                ("control", U1.ref, "s1"),
                ("observer", U1.ref, "s1"),
                ("peer", U2.ref, "s2"),
            )
        },
    }


def test_service_identity_snapshot_is_derived_from_host():
    data = configuration()
    catalog, callers = host_configuration(data)
    snapshot = json.loads(catalog.records[0].configuration)["identities"]
    # JSON 编码会把不可变元组转为数组，结构经同一解析器校验。
    assert snapshot == json.loads(json.dumps(data["identities"]))
    assert callers["peer"].principal == U2.ref
    data["launches"][0]["configuration"] = catalog.records[0].configuration
    assert host_configuration(data)[0].records == catalog.records


@pytest.mark.parametrize(
    "target,principal,scope",
    [
        ("caller", "unknown", "s1"),
        ("caller", U2.ref, "s1"),
        ("service", A1.ref, "s2"),
        ("service", "unknown", None),
    ],
)
def test_unknown_or_unentitled_bindings_fail_before_catalog_creation(target, principal, scope):
    data = configuration()
    value = data["callers"]["peer"] if target == "caller" else data["launches"][0]["binding"]
    value.update(principal=principal, resolution_scope=scope)
    with pytest.raises(ValueError):
        host_configuration(data)


def test_independently_valid_but_broader_group_snapshot_is_rejected():
    data = configuration()
    stale = json.loads(json.dumps(data["identities"]))
    stale["groups"][0]["members"].append(U2.ref)
    data["launches"][0]["configuration"] = json.dumps(
        {
            "provider": "namespace",
            "administrator": U1.ref,
            "identities": stale,
        }
    )
    with pytest.raises(ValueError, match="不一致"):
        host_configuration(data)


def test_service_administrator_cannot_drift_from_control_binding():
    data = configuration()
    data["launches"][0]["configuration"] = json.dumps(
        {"provider": "namespace", "administrator": U2.ref}
    )
    with pytest.raises(ValueError, match="管理主体"):
        host_configuration(data)
