"""
Tests for detecting a model's Python dependencies and rendering them as a requirements.txt.

Detection resolves import names against the *running* environment, so tests that need a
real distribution use ones this repo already depends on: PyYAML (imported as `yaml`, a
renamed distribution) and pytest (imported as its own name).
"""

import importlib.metadata
import re

from pymerlin._internal import _requirements


def scan(tmp_path, source, name="model.py", local_names=()):
    model = tmp_path / name
    model.write_text(source)
    return _requirements.generate([model], set(local_names))


# --- what counts as a dependency -------------------------------------------


def test_third_party_import_is_pinned_to_the_installed_version(tmp_path):
    generated = scan(tmp_path, "import yaml\n")

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_from_import_resolves_the_same_way(tmp_path):
    generated = scan(tmp_path, "from yaml import safe_load\n")

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_submodule_import_pins_the_distribution_once(tmp_path):
    generated = scan(tmp_path, "import yaml.parser\nfrom yaml.nodes import Node\nimport yaml\n")

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_stdlib_imports_are_not_requirements(tmp_path):
    generated = scan(tmp_path, "import json\nimport os.path\nfrom pathlib import Path\n")

    assert generated.requirements == []
    assert generated.text is None


def test_worker_provided_packages_are_not_requirements(tmp_path):
    generated = scan(tmp_path, "import numpy\nimport spiceypy\nfrom pymerlin import MissionModel\n")

    assert generated.requirements == []
    assert sorted(generated.provided) == ["numpy", "pymerlin", "spiceypy"]


def test_relative_imports_are_not_requirements(tmp_path):
    generated = scan(tmp_path, "from . import sibling\nfrom .deeper.mod import thing\n")

    assert generated.requirements == []


def test_local_module_names_are_not_requirements(tmp_path):
    generated = scan(tmp_path, "import helpers\nfrom my_model.util import x\n",
                     local_names={"helpers", "my_model"})

    assert generated.requirements == []


def test_package_importing_itself_by_its_installed_name(tmp_path):
    # The directory is named 'aerie-orbiter-model' but the package installs as
    # 'orbiter_model', and its modules import each other by that name.
    pkg = tmp_path / "aerie-orbiter-model"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "battery.py").write_text("")
    model = pkg / "mission.py"
    model.write_text("from orbiter_model.battery import Battery\nimport orbiter_model\n")

    generated = _requirements.generate([model], {"aerie-orbiter-model"}, package_root=pkg)

    assert generated.requirements == []


def test_self_reference_needs_the_tail_to_exist(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    model = pkg / "mission.py"
    model.write_text("from some_package.absent import thing\n")

    generated = _requirements.generate([model], {"pkg"}, package_root=pkg)

    assert generated.requirements == ["some_package"]


def test_installed_package_beats_the_self_reference_heuristic(tmp_path):
    # The package has a parser.py, so 'yaml.parser' looks like a self-reference — but
    # PyYAML is installed under the name 'yaml', which settles it.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "parser.py").write_text("")
    model = pkg / "mission.py"
    model.write_text("from yaml.parser import Parser\n")

    generated = _requirements.generate([model], {"pkg"}, package_root=pkg)

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_imports_inside_functions_are_found(tmp_path):
    source = (
        "def loader():\n"
        "    import yaml\n"
        "    return yaml\n"
    )
    generated = scan(tmp_path, source)

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_imports_inside_try_blocks_are_found(tmp_path):
    source = (
        "try:\n"
        "    import yaml\n"
        "except ImportError:\n"
        "    yaml = None\n"
    )
    generated = scan(tmp_path, source)

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_type_checking_imports_are_skipped(tmp_path):
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import yaml\n"
    )
    generated = scan(tmp_path, source)

    assert generated.requirements == []


def test_qualified_type_checking_guard_is_skipped(tmp_path):
    source = (
        "import typing\n"
        "if typing.TYPE_CHECKING:\n"
        "    import yaml\n"
    )
    generated = scan(tmp_path, source)

    assert generated.requirements == []


def test_else_branch_of_a_type_checking_guard_still_counts(tmp_path):
    # The else branch is the one that runs, so its imports do need installing.
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import json\n"
        "else:\n"
        "    import yaml\n"
    )
    generated = scan(tmp_path, source)

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]


def test_unresolvable_import_is_guessed_and_warned_about(tmp_path):
    generated = scan(tmp_path, "import nonexistent_package_xyz\n")

    assert generated.requirements == ["nonexistent_package_xyz"]
    assert any("assumed 'nonexistent_package_xyz'" in w for w in generated.warnings)


def test_known_alias_is_used_when_the_package_is_absent(tmp_path):
    # cv2 is not installed here, so resolution falls through to the alias table.
    generated = scan(tmp_path, "import cv2\n")

    assert generated.requirements == ["opencv-python"]


def test_compiled_extension_packages_are_warned_about(tmp_path):
    generated = scan(tmp_path, "import yaml\n")

    assert any("compiled extensions" in w and "PyYAML" in w for w in generated.warnings)


def test_pure_python_package_is_not_warned_about(tmp_path):
    generated = scan(tmp_path, "import iniconfig\n")

    assert generated.requirements == [f"iniconfig=={importlib.metadata.version('iniconfig')}"]
    assert generated.warnings == []


def test_requirements_are_sorted_case_insensitively(tmp_path):
    generated = scan(tmp_path, "import yaml\nimport iniconfig\nimport pytest\n")

    assert generated.requirements == sorted(generated.requirements, key=str.lower)


def test_unparseable_file_warns_without_failing(tmp_path):
    generated = scan(tmp_path, "import yaml\nthis is not python(\n")

    assert generated.requirements == []
    assert any("could not scan model.py" in w for w in generated.warnings)


def test_scan_continues_past_an_unparseable_file(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("import yaml\n")
    bad = tmp_path / "bad.py"
    bad.write_text("def (\n")

    generated = _requirements.generate([good, bad], set())

    assert generated.requirements == [f"PyYAML=={importlib.metadata.version('PyYAML')}"]
    assert any("could not scan bad.py" in w for w in generated.warnings)


def test_generated_text_lists_requirements_after_a_header(tmp_path):
    generated = scan(tmp_path, "import yaml\n")

    lines = generated.text.splitlines()
    assert lines[0].startswith("# Generated by 'pymerlin package'")
    assert "model.py" in lines[1]
    assert [line for line in lines if not line.startswith("#")] == generated.requirements
    assert generated.text.endswith("\n")


def test_header_wraps_a_long_file_list(tmp_path):
    files = []
    for i in range(20):
        source = tmp_path / f"module_with_a_longish_name_{i}.py"
        source.write_text("import yaml\n" if i == 0 else "")
        files.append(source)

    generated = _requirements.generate(files, set())

    assert all(len(line) <= 80 for line in generated.text.splitlines())
    assert_installable(generated.text)


def test_generated_text_is_a_file_pip_can_read(tmp_path):
    generated = scan(tmp_path, "import yaml\nimport iniconfig\n")

    assert_installable(generated.text)


def assert_installable(text):
    """
    Every non-comment line is a plain pinned requirement pip will accept.

    Nothing generated should ever be an editable install, a VCS or path URL, a `-r`
    include or a bare pip option — those are the shapes a hand-written file could take,
    and this is the check that generation cannot start emitting them.
    """
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        assert re.fullmatch(r"[A-Za-z0-9._-]+(==[A-Za-z0-9._+!-]+)?", line), line
