"""
Working out which Python packages a model needs, and rendering them as a requirements.txt.

A packaged model runs against the worker image's GraalPy venv, which ships a fixed set of
packages; anything else the model imports has to be installed there before the model is
loaded, so the JAR has to declare what it needs. The model's own import statements already
are that declaration, so they are what we read.

This is the only way a requirements.txt gets into a model JAR. There is no hand-written
alternative to fall back on, which is a deliberate trade: a dependency file is one more
thing to keep in sync with the code, and a list derived from the source cannot drift out of
date the way a maintained one can. The cost is that whatever this module cannot see, the
model cannot depend on — see the caveats on _scan_imports and _installed_distribution.
"""

import ast
import importlib.metadata
import sys
import textwrap
from typing import NamedTuple


# Where a bundled requirements.txt lives inside the JAR, and the manifest attribute
# announcing it. The Java shim reads the attribute to decide whether to look for the
# entry at all, so both sides must agree on these two strings.
JAR_ENTRY = "pymerlin_requirements.txt"
MANIFEST_ATTR = "Pymerlin-Requirements"

# Already in the worker's GraalPy venv, provisioned at image build time. Naming these in a
# generated file would be worse than leaving them out: the version pin would come from the
# author's CPython environment, and asking the worker for a numpy other than the image's
# turns a no-op install into a source rebuild that takes many minutes.
_WORKER_PROVIDED = {"pymerlin", "numpy", "spiceypy"}

# Import name -> PyPI distribution, for the well-known cases where they differ. Consulted
# only when the package is not installed alongside the model: a local install answers the
# question exactly (see _installed_distribution), while this table is a fixed guess about a
# moving world, so it stays short and uncontroversial.
_DISTRIBUTION_ALIASES = {
    "attr": "attrs",
    "attrs": "attrs",
    "bs4": "beautifulsoup4",
    "Crypto": "pycryptodome",
    "cv2": "opencv-python",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "OpenGL": "PyOpenGL",
    "PIL": "Pillow",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "zmq": "pyzmq",
}


class GeneratedRequirements(NamedTuple):
    """
    The outcome of scanning a model's source for third-party imports.

    `text` is the full requirements.txt to bundle, or None when the model needs nothing
    beyond what the worker already provides — in which case no file is bundled at all and
    packaging behaves as it did before this existed.
    """
    text: "str | None"
    requirements: list      # requirement lines alone ("pandas==2.1.0"), for logging
    provided: list          # imports the worker image already satisfies, for logging
    imports: list           # every top-level name imported, before any filtering
    warnings: list


def generate(files, local_names, package_root=None) -> GeneratedRequirements:
    """
    Scan `files` for imports and build the requirements.txt the model needs.

    `local_names` are top-level module names that resolve inside the bundle itself (the
    model's own package and its sibling modules); they ship in the JAR, so they must never
    be mistaken for something to install. `package_root` is the bundled package directory,
    if the model is one, which lets a package's absolute imports of its own modules be
    recognized as well (see _resolves_inside).
    """
    imports, internal, warnings = _scan_imports(files, package_root)

    provided = []
    requirements = []
    compiled = []
    for name in sorted(imports):
        if name in sys.stdlib_module_names or name in local_names:
            continue
        if name in _WORKER_PROVIDED:
            provided.append(name)
            continue

        # An installed package is hard evidence and settles the question. It is checked
        # before `internal` deliberately: a model package holding a `parser.py` would
        # otherwise make `from yaml.parser import ...` look like a self-reference and drop
        # PyYAML from the requirements, and a missing package is the failure that hurts.
        distribution, version = _installed_distribution(name)
        if distribution is None and name in internal:
            continue
        if distribution is None:
            # Nothing installed to check against, so fall back to the alias table and then
            # to the import name itself. That guess is usually right (most packages import
            # as their own name), but when it is wrong pip fails on the worker with a name
            # the author never wrote, so say plainly where it came from.
            distribution = _DISTRIBUTION_ALIASES.get(name)
            if distribution is None:
                distribution = name
                warnings.append(
                    f"'{name}' is not installed here, so pymerlin cannot tell which PyPI "
                    f"package provides it, or at what version, and assumed '{name}'. "
                    "Install it into the environment you package from to pin it exactly"
                )
        if distribution in _WORKER_PROVIDED:
            provided.append(distribution)
            continue

        if version is None:
            requirements.append(distribution)
        else:
            requirements.append(f"{distribution}=={version}")
            if _ships_compiled_extensions(distribution):
                compiled.append(distribution)

    if compiled:
        warnings.append(
            "these packages ship compiled extensions, which are not portable from CPython "
            "to the worker's GraalPy: " + ", ".join(compiled) + ". They install only if "
            "GraalPy's wheel repository has a build, or if they compile from source on the "
            "worker (slow, and not every package manages it)"
        )

    # Sorted by distribution name so the generated file is stable across runs: it is
    # bundled into the JAR, and a JAR that changes only in line order is a false diff.
    requirements.sort(key=str.lower)
    text = _render(requirements, files) if requirements else None
    return GeneratedRequirements(text, requirements, provided, sorted(imports), warnings)


def _render(requirements, files) -> str:
    """Render the generated requirements.txt, header comments and all."""
    scanned = ", ".join(sorted(f.name for f in files))
    lines = ["# Generated by 'pymerlin package' from the imports in:"]
    lines.extend("#   " + line for line in textwrap.wrap(scanned, width=76))
    lines.extend([
        "#",
        "# Versions are pinned to what was installed alongside the model when it was",
        "# packaged. Do not edit: 'pymerlin package' rewrites this file from the",
        "# model's imports every time. To change what the worker installs, change",
        "# what the model imports.",
    ])
    lines.extend(requirements)
    return "\n".join(lines) + "\n"


def _scan_imports(files, package_root):
    """
    Return (top-level import names, names that are really the package itself, warnings).

    Imports nested in functions, classes and try/except blocks all count — they run when
    the model runs. Imports reached only by `importlib.import_module` or `__import__` are
    invisible to any static scan, and with no hand-written requirements.txt to fall back
    on there is no way to declare them: such a model fails on the worker. A model that
    needs a package must import it by name somewhere.

    Every file that ships in the JAR is scanned, including any that the model itself never
    imports. Erring toward installing a package the model turns out not to need is the
    cheap mistake; the expensive one is a missing package, which fails the simulation.
    """
    names = set()
    internal = set()
    warnings = []

    def record(dotted):
        names.add(dotted.split(".")[0])
        if _resolves_inside(package_root, dotted):
            internal.add(dotted.split(".")[0])

    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        except SyntaxError as e:
            # A model that does not parse cannot be scanned, but it can still be packaged:
            # the author may be mid-edit, and failing the whole package command over a file
            # pymerlin only wanted to read imports from would be its own annoyance.
            warnings.append(f"could not scan {file.name} for imports ({e.msg}, line {e.lineno})")
            continue
        except OSError as e:
            warnings.append(f"could not read {file.name} ({e})")
            continue

        type_only = _type_checking_imports(tree)
        for node in ast.walk(tree):
            if node in type_only:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    record(alias.name)
            # A relative import (level > 0) resolves inside the bundled package, and
            # `from . import x` has no module name at all.
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                record(node.module)
    return names, internal, warnings


def _resolves_inside(package_root, dotted_name) -> bool:
    """
    Whether a dotted import names a module of the bundled package under a top-level name
    other than the package directory's.

    A package directory is routinely named differently from the package it installs as —
    'aerie-orbiter-model' holding 'orbiter_model' — and its modules import each other
    absolutely, by that installed name. Matching everything after the first segment against
    the package's own files recognizes 'from orbiter_model.battery import Battery' as a
    self-reference rather than a PyPI package nobody publishes.
    """
    if package_root is None:
        return False
    parts = dotted_name.split(".")[1:]
    if not parts:
        return False
    target = package_root.joinpath(*parts)
    return target.with_suffix(".py").is_file() or (target / "__init__.py").is_file()


def _type_checking_imports(tree):
    """
    Import nodes guarded by `if TYPE_CHECKING:`, which never execute.

    Installing a package the model only imports for type annotations would be a pure false
    positive — and on GraalPy a costly one if it happens to carry compiled extensions.
    """
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_type_checking(node.test):
            continue
        # Only the body is skipped. An `else:` branch of a TYPE_CHECKING guard is the
        # runtime path, and its imports are exactly the ones that do need installing.
        for statement in node.body:
            for child in ast.walk(statement):
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    guarded.add(child)
    return guarded


def _is_type_checking(test) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def _installed_distribution(import_name):
    """
    Map a top-level import name to the (distribution, version) installed under it here.

    The environment the model was written in is the best evidence available: if `yaml` is
    importable here it came from some installed distribution, and the metadata says which
    one and at what version. Returns (None, None) when nothing installed provides the name.
    """
    for distribution in _installed_distributions().get(import_name, ()):
        try:
            return distribution, importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None, None


_installed_cache = None


def _installed_distributions():
    """Top-level import name -> distributions providing it, for this environment."""
    global _installed_cache
    if _installed_cache is None:
        _installed_cache = importlib.metadata.packages_distributions()
    return _installed_cache


def _ships_compiled_extensions(distribution) -> bool:
    try:
        files = importlib.metadata.files(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return any(str(f).endswith((".so", ".pyd", ".dylib")) for f in (files or ()))
