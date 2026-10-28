from __future__ import annotations

from pathlib import Path

import pytest
from ghrah.scheme.loading.manifest_fields import (
    DuplicateManifestError,
    ManifestNotFoundError,
    ManifestValidationError,
)

from ghrah.subject.manifest_store.builtins import ensure_builtins
from ghrah.subject.manifest_store.service import handle_manifest_command
from ghrah.subject.manifest_store.store import ManifestStore

VALID_ABILITY_YAML = """\
manifest: ability
version: "1"
metadata:
  namespace: ghrah.fs
  name: read_file
  description: Read file contents
  permissions:
    fs_read_only: true
tool:
  name: read_file
  description: Read the contents of a file
  parameters:
    file_path:
      type: string
      description: Path to the file
      required: true
implementation:
  type: builtin
  handler: read_file
"""

VALID_ABILITY_YAML_2 = """\
manifest: ability
version: "1"
metadata:
  namespace: ghrah.fs
  name: write_file
  description: Write file contents
  permissions:
    fs_write: true
tool:
  name: write_file
  description: Write contents to a file
  parameters:
    file_path:
      type: string
      description: Path to the file
      required: true
implementation:
  type: builtin
  handler: write_file
"""

VALID_AGENT_YAML = """\
manifest: agent
version: "1"
metadata:
  namespace: my_project
  name: dev_agent
  description: A dev agent
model:
  agent_config_name: gpt-4o-dev
system_prompt: You are a dev agent.
abilities:
  - type: conversation
"""

VALID_AGENT_YAML_2 = """\
manifest: agent
version: "1"
metadata:
  namespace: my_project
  name: test_agent
  description: A test agent
model:
  agent_config_name: gpt-4o-mini
system_prompt: You are a test agent.
abilities:
  - type: end_task
"""

INVALID_YAML_CONTENT = """\
manifest: ability
version: "99"
metadata:
  namespace: ghrah.fs
  name: bad_ability
description: Missing nested fields
implementation:
  type: builtin
"""

INVALID_MANIFEST_TYPE = """\
manifest: unknown_type
version: "1"
metadata:
  namespace: ghrah.fs
  name: something
description: Something
"""


@pytest.fixture
def store(tmp_path: Path) -> ManifestStore:
    s = ManifestStore(tmp_path / "manifests")
    s.ensure_dirs()
    return s


class TestManifestStoreInit:
    def test_ensure_dirs_creates_structure(self, tmp_path: Path) -> None:
        root = tmp_path / "manifests"
        store = ManifestStore(root)
        store.ensure_dirs()
        assert (root / "abilities").is_dir()
        assert (root / "agents").is_dir()

    def test_ensure_dirs_idempotent(self, tmp_path: Path) -> None:
        root = tmp_path / "manifests"
        store = ManifestStore(root)
        store.ensure_dirs()
        store.ensure_dirs()
        assert (root / "abilities").is_dir()


class TestManifestStoreAbilityCRUD:
    def test_put_and_get_ability(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        manifest = store.get_ability("ghrah.fs.read_file")
        assert manifest.manifest == "ability"
        assert manifest.metadata.namespace == "ghrah.fs"
        assert manifest.metadata.name == "read_file"
        assert manifest.full_name == "ghrah.fs.read_file"

    def test_put_ability_creates_namespace_dir(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        ns_dir = store._abilities_dir / "ghrah.fs"
        assert ns_dir.is_dir()
        assert (ns_dir / "read_file.yaml").exists()

    def test_list_abilities_empty(self, store: ManifestStore) -> None:
        result = store.list_abilities()
        assert result == []

    def test_list_abilities_all(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        store.put_ability("ghrah.fs.write_file", VALID_ABILITY_YAML_2)
        result = store.list_abilities()
        assert "ghrah.fs.read_file" in result
        assert "ghrah.fs.write_file" in result

    def test_list_abilities_by_namespace(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        store.put_ability(
            "my_project.my_ability",
            VALID_ABILITY_YAML.replace("ghrah.fs", "my_project").replace("read_file", "my_ability"),
        )
        result = store.list_abilities(namespace="ghrah.fs")
        assert result == ["ghrah.fs.read_file"]

    def test_list_abilities_nonexistent_namespace(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = store.list_abilities(namespace="nonexistent")
        assert result == []

    def test_delete_ability(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        store.delete_ability("ghrah.fs.read_file")
        with pytest.raises(ManifestNotFoundError):
            store.get_ability("ghrah.fs.read_file")

    def test_delete_nonexistent_ability_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestNotFoundError):
            store.delete_ability("ghrah.fs.nonexistent")

    def test_put_ability_duplicate_raises(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        with pytest.raises(DuplicateManifestError):
            store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML, overwrite=False)

    def test_put_ability_overwrite(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        modified = VALID_ABILITY_YAML.replace("Read file contents", "Read file contents v2")
        store.put_ability("ghrah.fs.read_file", modified, overwrite=True)
        manifest = store.get_ability("ghrah.fs.read_file")
        assert manifest.metadata.description == "Read file contents v2"

    def test_put_ability_invalid_content_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_ability("ghrah.fs.bad", INVALID_YAML_CONTENT)

    def test_put_ability_wrong_type_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_ability("my_project.agent", VALID_AGENT_YAML)

    def test_validate_ability_valid(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_ability(VALID_ABILITY_YAML)
        assert is_valid is True
        assert errors == []

    def test_validate_ability_invalid(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_ability(INVALID_YAML_CONTENT)
        assert is_valid is False
        assert len(errors) > 0

    def test_get_ability_nonexistent(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestNotFoundError):
            store.get_ability("ghrah.fs.nonexistent")

    def test_get_ability_source(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        source = store.get_ability_source("ghrah.fs.read_file")
        assert isinstance(source, str)
        assert "ghrah.fs" in source
        assert "read_file" in source

    def test_get_ability_source_nonexistent(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestNotFoundError):
            store.get_ability_source("ghrah.fs.nonexistent")


class TestManifestStoreAgentCRUD:
    def test_put_and_get_agent(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        manifest = store.get_agent("my_project.dev_agent")
        assert manifest.manifest == "agent"
        assert manifest.metadata.namespace == "my_project"
        assert manifest.metadata.name == "dev_agent"
        assert manifest.full_name == "my_project.dev_agent"

    def test_list_agents_all(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        store.put_agent("my_project.test_agent", VALID_AGENT_YAML_2)
        result = store.list_agents()
        assert len(result) == 2

    def test_list_agents_by_namespace(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result = store.list_agents(namespace="my_project")
        assert "my_project.dev_agent" in result

    def test_delete_agent(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        store.delete_agent("my_project.dev_agent")
        with pytest.raises(ManifestNotFoundError):
            store.get_agent("my_project.dev_agent")

    def test_put_agent_duplicate_raises(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        with pytest.raises(DuplicateManifestError):
            store.put_agent("my_project.dev_agent", VALID_AGENT_YAML, overwrite=False)

    def test_put_agent_overwrite(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        modified = VALID_AGENT_YAML.replace("You are a dev agent.", "You are a dev agent v2.")
        store.put_agent("my_project.dev_agent", modified, overwrite=True)
        manifest = store.get_agent("my_project.dev_agent")
        assert manifest.system_prompt == "You are a dev agent v2."

    def test_put_agent_invalid_content_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_agent("my_project.bad", INVALID_YAML_CONTENT)

    def test_put_agent_wrong_type_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_agent("ghrah.fs.read_file", VALID_ABILITY_YAML)

    def test_get_agent_source(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        source = store.get_agent_source("my_project.dev_agent")
        assert isinstance(source, str)
        assert "my_project" in source
        assert "dev_agent" in source

    def test_get_agent_source_nonexistent(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestNotFoundError):
            store.get_agent_source("my_project.nonexistent")


class TestManifestStorePathMapping:
    def test_ability_path_with_namespace(self, store: ManifestStore) -> None:
        path = store._ability_path("ghrah.fs.read_file")
        assert path == store._abilities_dir / "ghrah.fs" / "read_file.yaml"

    def test_agent_path_with_namespace(self, store: ManifestStore) -> None:
        path = store._agent_path("my_project.dev_agent")
        assert path == store._agents_dir / "my_project" / "dev_agent.yaml"

    def test_ability_path_no_namespace(self, store: ManifestStore) -> None:
        path = store._ability_path("conversation")
        assert path == store._abilities_dir / "default" / "conversation.yaml"

    def test_put_ability_simple_name(self, store: ManifestStore) -> None:
        simple_yaml = VALID_ABILITY_YAML.replace(
            "namespace: ghrah.fs", "namespace: default"
        ).replace("name: read_file", "name: conversation")
        simple_yaml = simple_yaml.replace("handler: read_file", "handler: conversation")
        simple_yaml = simple_yaml.replace("name: read_file", "name: conversation")
        simple_yaml = simple_yaml.replace("Read file contents", "Conversation")
        simple_yaml = simple_yaml.replace("Read the contents of a file", "Conversation ability")
        store.put_ability("conversation", simple_yaml)
        manifest = store.get_ability("conversation")
        assert manifest.metadata.namespace == "default"

    def test_invalid_namespace_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_ability("INVALID.Name", VALID_ABILITY_YAML)

    def test_invalid_name_raises(self, store: ManifestStore) -> None:
        with pytest.raises(ManifestValidationError):
            store.put_ability("ghrah.fs.BAD_NAME", VALID_ABILITY_YAML)


class TestManifestStoreValidateManifest:
    def test_validate_valid_ability(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_manifest(VALID_ABILITY_YAML)
        assert is_valid is True

    def test_validate_valid_agent(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_manifest(VALID_AGENT_YAML)
        assert is_valid is True

    def test_validate_invalid_type(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_manifest(INVALID_MANIFEST_TYPE)
        assert is_valid is False
        assert any(
            "Unknown" in e or "unknown" in e.lower() or "missing" in e.lower() for e in errors
        )

    def test_validate_invalid_yaml_syntax(self, store: ManifestStore) -> None:
        is_valid, errors = store.validate_manifest(":::invalid{{yaml")
        assert is_valid is False


class TestEnsureBuiltins:
    def test_ensure_builtins_writes_all(self, store: ManifestStore) -> None:
        written = ensure_builtins(store)
        assert len(written) >= 9
        assert "ghrah.core.conversation" in written

    def test_ensure_builtins_idempotent(self, store: ManifestStore) -> None:
        written1 = ensure_builtins(store)
        written2 = ensure_builtins(store)
        assert len(written1) >= 9
        assert len(written2) == 0

    def test_ensure_builtins_ability_retrievable(self, store: ManifestStore) -> None:
        ensure_builtins(store)
        manifest = store.get_ability("ghrah.fs.read_file")
        assert manifest.manifest == "ability"
        assert manifest.metadata.namespace == "ghrah.fs"


class TestManifestStoreProtocol:
    def test_manifest_store_has_protocol_methods(self, store: ManifestStore) -> None:
        assert hasattr(store, "load_ability")
        assert hasattr(store, "load_agent")
        assert hasattr(store, "list_abilities")
        assert hasattr(store, "list_agents")
        assert callable(store.load_ability)
        assert callable(store.load_agent)
        assert callable(store.list_abilities)
        assert callable(store.list_agents)

    def test_load_ability_delegates_to_get(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result_via_load = store.load_ability("ghrah.fs.read_file")
        result_via_get = store.get_ability("ghrah.fs.read_file")
        assert result_via_load.full_name == result_via_get.full_name

    def test_load_agent_delegates_to_get(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result_via_load = store.load_agent("my_project.dev_agent")
        result_via_get = store.get_agent("my_project.dev_agent")
        assert result_via_load.full_name == result_via_get.full_name

    def test_list_abilities_protocol(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = store.list_abilities()
        assert "ghrah.fs.read_file" in result

    def test_list_agents_protocol(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result = store.list_agents()
        assert "my_project.dev_agent" in result


class TestManifestStoreListingEmpty:
    def test_list_abilities_no_dir(self, tmp_path: Path) -> None:
        store = ManifestStore(tmp_path / "nonexistent")
        result = store.list_abilities()
        assert result == []

    def test_list_agents_no_dir(self, tmp_path: Path) -> None:
        store = ManifestStore(tmp_path / "nonexistent")
        result = store.list_agents()
        assert result == []


class TestManifestServiceCommand:
    def test_list_abilities(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = handle_manifest_command("manifest_list_abilities", {}, store)
        assert result["success"] is True
        assert "ghrah.fs.read_file" in [a["full_name"] for a in result["data"]["abilities"]]

    def test_list_abilities_with_namespace(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = handle_manifest_command(
            "manifest_list_abilities", {"namespace": "ghrah.fs"}, store
        )
        assert result["success"] is True

    def test_get_ability(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = handle_manifest_command(
            "manifest_get_ability", {"full_name": "ghrah.fs.read_file"}, store
        )
        assert result["success"] is True
        assert result["data"]["manifest"]["manifest"] == "ability"
        assert "source" in result["data"]
        assert isinstance(result["data"]["source"], str)
        assert "ghrah.fs" in result["data"]["source"]

    def test_get_ability_not_found(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_get_ability", {"full_name": "ghrah.fs.nonexistent"}, store
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_put_ability(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_put_ability",
            {"full_name": "ghrah.fs.read_file", "content": VALID_ABILITY_YAML},
            store,
        )
        assert result["success"] is True
        assert result["data"]["full_name"] == "ghrah.fs.read_file"
        assert "manifest" in result["data"]
        assert "source" in result["data"]

    def test_put_ability_duplicate(self, store: ManifestStore) -> None:
        handle_manifest_command(
            "manifest_put_ability",
            {"full_name": "ghrah.fs.read_file", "content": VALID_ABILITY_YAML},
            store,
        )
        result = handle_manifest_command(
            "manifest_put_ability",
            {"full_name": "ghrah.fs.read_file", "content": VALID_ABILITY_YAML},
            store,
        )
        assert result["success"] is False

    def test_put_ability_overwrite(self, store: ManifestStore) -> None:
        handle_manifest_command(
            "manifest_put_ability",
            {"full_name": "ghrah.fs.read_file", "content": VALID_ABILITY_YAML},
            store,
        )
        modified = VALID_ABILITY_YAML.replace("Read file contents", "Read file contents v2")
        result = handle_manifest_command(
            "manifest_put_ability",
            {
                "full_name": "ghrah.fs.read_file",
                "content": modified,
                "overwrite": True,
            },
            store,
        )
        assert result["success"] is True

    def test_delete_ability(self, store: ManifestStore) -> None:
        store.put_ability("ghrah.fs.read_file", VALID_ABILITY_YAML)
        result = handle_manifest_command(
            "manifest_delete_ability",
            {"full_name": "ghrah.fs.read_file"},
            store,
        )
        assert result["success"] is True

    def test_list_agents(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result = handle_manifest_command("manifest_list_agents", {}, store)
        assert result["success"] is True
        assert "my_project.dev_agent" in [a["full_name"] for a in result["data"]["agents"]]

    def test_get_agent(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result = handle_manifest_command(
            "manifest_get_agent", {"full_name": "my_project.dev_agent"}, store
        )
        assert result["success"] is True
        assert result["data"]["manifest"]["manifest"] == "agent"
        assert "source" in result["data"]
        assert isinstance(result["data"]["source"], str)
        assert "my_project" in result["data"]["source"]

    def test_put_agent(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_put_agent",
            {"full_name": "my_project.dev_agent", "content": VALID_AGENT_YAML},
            store,
        )
        assert result["success"] is True
        assert "manifest" in result["data"]
        assert "source" in result["data"]

    def test_delete_agent(self, store: ManifestStore) -> None:
        store.put_agent("my_project.dev_agent", VALID_AGENT_YAML)
        result = handle_manifest_command(
            "manifest_delete_agent",
            {"full_name": "my_project.dev_agent"},
            store,
        )
        assert result["success"] is True

    def test_validate_valid(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_validate",
            {"content": VALID_ABILITY_YAML},
            store,
        )
        assert result["success"] is True
        assert result["data"]["valid"] is True

    def test_validate_invalid(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_validate",
            {"content": INVALID_YAML_CONTENT},
            store,
        )
        assert result["success"] is True
        assert result["data"]["valid"] is False

    def test_unknown_command(self, store: ManifestStore) -> None:
        result = handle_manifest_command("manifest_unknown", {}, store)
        assert result["success"] is False
        assert "Unknown" in result["error"]

    def test_put_invalid_manifest_content(self, store: ManifestStore) -> None:
        result = handle_manifest_command(
            "manifest_put_ability",
            {"full_name": "ghrah.fs.bad", "content": INVALID_YAML_CONTENT},
            store,
        )
        assert result["success"] is False
