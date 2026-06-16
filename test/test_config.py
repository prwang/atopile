from pathlib import Path
from textwrap import dedent

import pytest
import semver
from pydantic.networks import HttpUrl

from atopile.config import (
    PROJECT_CONFIG_FILENAME,
    BuildTargetPaths,
    FileDependencySpec,
    ProjectPaths,
    RegistryDependencySpec,
    config,
)

TEST_CONFIG_TEXT = dedent(
    """
    requires-atopile: ^0.2.0
    package:
      identifier: pepper-labs/my-project
      version: 1.2.3
      repository: https://github.com/pepper-labs/my-project
    builds:
      debug:
        entry: elec/src/debug.ato:Debug
    # comments
    dependencies:
    - atopile/tps63020dsjr # comments
    - atopile/usb-connectors@v2.0.1
    - atopile/esp32-s3
    - type: file
      identifier: atopile/rp2040
      path: ../rp2040
    """
).lstrip()


def test_roundtrip(tmp_path: Path):
    config_path = tmp_path / PROJECT_CONFIG_FILENAME
    config_path.write_text(TEST_CONFIG_TEXT, encoding="utf-8")
    config.project_dir = tmp_path

    assert config.project.requires_atopile == "^0.2.0"
    assert config.project.package is not None
    assert config.project.package.version == semver.Version.parse("1.2.3")
    assert config.project.package.repository == HttpUrl(
        "https://github.com/pepper-labs/my-project"
    )
    assert config.project.package.identifier == "pepper-labs/my-project"
    assert config.project.dependencies is not None
    assert config.project.dependencies[0].identifier == "atopile/tps63020dsjr"
    assert config.project.dependencies[1].identifier == "atopile/usb-connectors"
    assert isinstance(config.project.dependencies[1], RegistryDependencySpec)
    assert config.project.dependencies[1].release == "v2.0.1"
    assert config.project.dependencies[2].identifier == "atopile/esp32-s3"
    assert config.project.dependencies[3].identifier == "atopile/rp2040"
    assert isinstance(config.project.dependencies[3], FileDependencySpec)
    assert config.project.dependencies[3].path == Path("../rp2040")

    config.update_project_settings(lambda data, new_data: data, {})

    assert config_path.read_text(encoding="utf-8") == TEST_CONFIG_TEXT


def test_update_project_config(tmp_path: Path):
    config_path = tmp_path / PROJECT_CONFIG_FILENAME
    config_path.write_text(TEST_CONFIG_TEXT, encoding="utf-8")
    config.project_dir = tmp_path

    # Make some changes and check that they are reflected in the config
    dep1 = RegistryDependencySpec(identifier="usb-connectors", release="v0.0.1")
    dep2 = FileDependencySpec(identifier="esp32-s3", path=Path("../esp32-s3"))

    def add_dependency(config_data, new_data):
        config_data["dependencies"] = config_data["dependencies"] + [new_data]
        return config_data

    config.update_project_settings(add_dependency, dep1.model_dump())
    config.update_project_settings(add_dependency, dep2.model_dump())

    assert config.project.dependencies is not None

    assert isinstance(config.project.dependencies[4], RegistryDependencySpec)
    assert config.project.dependencies[4].release == "v0.0.1"
    assert isinstance(config.project.dependencies[5], FileDependencySpec)
    assert config.project.dependencies[5].path == Path("../esp32-s3")


# ===========================================================================
# D1 — layout.yaml path config (BACKLOG §D, task D1). S0 tests-first ratchet.
#
# `BuildTargetPaths` gains an optional `layout_config: Path | None = None` that
# carries the path to the build's layout.yaml (the layout-intent source, peer of
# `paths.layout` = the .kicad_pcb). Like the other build-target paths it is
# absolutized relative to the project root, and defaults to None when the build
# declares no layout.yaml. The build steps (D4) read it via
# `config.build.paths.layout_config`.
#
# Ratchet (S0 discipline, mirrors test_room_migration_contract `_C3_LANDED`):
# `_D1_LANDED` is a pure field probe — the field's presence on the model == the
# feature landed. strict-xfail until then, so an unimplemented field keeps the
# ratchet red and the flip to green is automatic on landing.
# ===========================================================================

_D1_LANDED = "layout_config" in BuildTargetPaths.model_fields
needs_d1 = pytest.mark.xfail(
    not _D1_LANDED,
    reason="D1 layout_config path field not landed (S0 ratchet)",
    strict=True,
)


@needs_d1
def test_layout_config_relative_path_is_absolutized(tmp_path: Path):
    """A relative `layout_config` resolves to an absolute path under the project
    root — same absolutization contract as the other build-target paths."""
    project_paths = ProjectPaths(root=tmp_path)
    paths = BuildTargetPaths(
        name="default",
        project_paths=project_paths,
        layout_config="layout.yaml",
    )
    assert paths.layout_config is not None
    assert paths.layout_config.is_absolute()
    assert paths.layout_config == (tmp_path / "layout.yaml").resolve().absolute()


@needs_d1
def test_layout_config_defaults_to_none(tmp_path: Path):
    """A build that declares no layout.yaml has `layout_config is None` — the
    feature is opt-in and never fabricates a path (loud-or-nothing: a default
    path would silently point at a nonexistent file)."""
    project_paths = ProjectPaths(root=tmp_path)
    paths = BuildTargetPaths(name="default", project_paths=project_paths)
    assert paths.layout_config is None
