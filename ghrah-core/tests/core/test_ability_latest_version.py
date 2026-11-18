"""最新版本判定：版本号必须按数值比较，而不是字符串。"""

from ghrah.core.read.ability_catalog import _GroupDraft, _group_view, _version_sort_key


def _draft(version: str) -> _GroupDraft:
    return _GroupDraft(
        namespace="acme",
        name="reporting",
        full_name="acme.reporting",
        title="报表",
        version=version,
        origin="installed",
        lock_state="locked",
    )


def test_version_sort_key_orders_numerically() -> None:
    versions = ["1.9.0", "1.10.0", "1.2.3"]
    assert sorted(versions, key=_version_sort_key) == ["1.2.3", "1.9.0", "1.10.0"]


def test_group_view_marks_numeric_max_as_latest() -> None:
    view = _group_view([_draft("1.9.0"), _draft("1.10.0"), _draft("1.2.3")], "distribution", {})

    assert view.latest_version == "1.10.0"
    assert [item.version for item in view.versions] == ["1.2.3", "1.9.0", "1.10.0"]
    assert [item.is_latest for item in view.versions] == [False, False, True]


def test_group_view_without_versions_has_no_latest() -> None:
    view = _group_view([_draft("")], "distribution", {})

    assert view.latest_version is None
    assert view.versions[0].is_latest is False