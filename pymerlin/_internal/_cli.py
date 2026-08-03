"""
Command-line interface for PyMerlin.
"""

import argparse
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from pymerlin._internal import _requirements


_SHIM_JAR = Path(__file__).parent / "jars" / "pymerlin-shim.jar"


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("pymerlin")
    except Exception:
        return "0.1.0-dev"


def main():
    parser = argparse.ArgumentParser(
        prog="pymerlin",
        description=(
            "pymerlin — Python mission modeling framework for PlanDev / Aerie.\n"
            "\n"
            "Write discrete-event simulation models in Python, then package them\n"
            "as uploadable PlanDev mission model JARs. At simulation time the\n"
            "model runs in-process on the PlanDev worker's embedded GraalPy\n"
            "interpreter — no subprocess, no serialization protocol."
        ),
        epilog=(
            "examples:\n"
            "  pymerlin package --model demo/model.py:Mission --out mission-model.jar\n"
            "\n"
            "documentation: https://mattdailis.github.io/pymerlin\n"
            "source:        https://github.com/mattdailis/pymerlin"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {_get_version()}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pkg_parser = subparsers.add_parser(
        "package",
        help="Package a Python model into an Aerie-compatible mission model JAR",
        description=(
            "Bundle a Python model file (and its package, if applicable) into a\n"
            "PlanDev-uploadable mission model JAR. The resulting JAR contains the\n"
            "prebuilt pymerlin-shim classes, your model source, and a manifest\n"
            "entry pointing at the model class.\n"
            "\n"
            "The model's third-party imports are detected and written into the JAR as a\n"
            "requirements.txt, which the worker pip-installs into its GraalPy venv before\n"
            "loading the model — so an import you add to the model needs no other step,\n"
            "and there is no dependency file to keep in sync. Versions are pinned to\n"
            "whatever is installed alongside the model when you package it.\n"
            "\n"
            "Not every PyPI package works under GraalPy: pure-Python ones generally do,\n"
            "C-extension ones need a GraalPy wheel or a source build on the worker."
        ),
        epilog=(
            "examples:\n"
            "  # Single-file model\n"
            "  pymerlin package --model model.py:Mission --out mission-model.jar\n"
            "\n"
            "  # Package-based model (bundles the whole package directory)\n"
            "  pymerlin package --model my_pkg/model.py:Mission --out mission-model.jar\n"
            "\n"
            "  # Ship no dependencies, whatever the model imports\n"
            "  pymerlin package --model model.py:Mission --out mission-model.jar \\\n"
            "      --no-requirements"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    pkg_parser.add_argument(
        "--model",
        required=True,
        help="Model reference: path/to/model.py:ClassName"
    )
    pkg_parser.add_argument(
        "--out",
        required=True,
        help="Output JAR file path"
    )
    pkg_parser.add_argument(
        "--bundle-model",
        action="store_true",
        default=True,
        help="Bundle the Python model file inside the JAR (default: true)"
    )
    pkg_parser.add_argument(
        "--no-requirements",
        action="store_true",
        help=(
            "Do not detect the model's imports and do not bundle a requirements.txt. "
            "The model then gets only the packages the worker image ships"
        )
    )

    args = parser.parse_args()

    if args.command == "package":
        _package(
            args.model,
            args.out,
            bundle_model=args.bundle_model,
            no_requirements=args.no_requirements,
        )


def _package(
    model_ref: str,
    output_jar: str,
    bundle_model: bool = True,
    no_requirements: bool = False,
):
    if not _SHIM_JAR.exists():
        print(f"[pymerlin] ERROR: shim JAR not found at {_SHIM_JAR}", file=sys.stderr)
        sys.exit(1)

    if ":" not in model_ref:
        print("[pymerlin] ERROR: --model must be 'path/to/file.py:ClassName'", file=sys.stderr)
        sys.exit(1)

    model_file_path, class_name = model_ref.rsplit(":", 1)
    model_file = Path(model_file_path).resolve()

    if not model_file.exists():
        print(f"[pymerlin] ERROR: model file not found: {model_file}", file=sys.stderr)
        sys.exit(1)

    pkg_dir = model_file.parent
    is_package = (pkg_dir / "__init__.py").exists()

    # The model ref stored in the JAR uses the bundled path if we bundle,
    # otherwise the absolute path on the host.
    if bundle_model:
        if is_package:
            jar_model_ref = f"pymerlin_models/{pkg_dir.name}/{model_file.name}:{class_name}"
        else:
            jar_model_ref = f"pymerlin_models/{model_file.name}:{class_name}"
    else:
        jar_model_ref = f"{model_file}:{class_name}"

    # Every .py that ships in the JAR — the same set the bundling loop below writes, and
    # the set scanned for imports.
    model_sources = sorted(pkg_dir.rglob("*.py")) if is_package else [model_file]

    print(f"[pymerlin] Packaging model: {model_ref}")
    print(f"[pymerlin] Shim JAR:        {_SHIM_JAR}")
    print(f"[pymerlin] Output:          {output_jar}")
    if is_package and bundle_model:
        print(f"[pymerlin] Package dir:     {pkg_dir}")

    requirements_text = None
    if not no_requirements:
        requirements_text = _generate_requirements(
            model_file=model_file,
            model_sources=model_sources,
            pkg_dir=pkg_dir,
            is_package=is_package,
            bundle_model=bundle_model,
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_jar = Path(tmpdir) / "output.jar"

        with zipfile.ZipFile(_SHIM_JAR, "r") as src, \
             zipfile.ZipFile(tmp_jar, "w", compression=zipfile.ZIP_DEFLATED) as dst:

            for item in src.infolist():
                data = src.read(item.filename)

                if item.filename == "META-INF/MANIFEST.MF":
                    # JAR manifest spec: lines use \r\n, max 72 bytes per line,
                    # and the file must end with a blank line (\r\n\r\n).
                    # Strip all trailing whitespace/newlines then rebuild cleanly.
                    manifest = data.decode("utf-8").rstrip("\r\n ")
                    manifest += f"\r\nPymerlin-Model-Ref: {jar_model_ref}\r\n"
                    if requirements_text is not None:
                        manifest += f"{_requirements.MANIFEST_ATTR}: {_requirements.JAR_ENTRY}\r\n"
                    manifest += "\r\n"
                    dst.writestr(item, manifest.encode("utf-8"))
                else:
                    dst.writestr(item, data)

            if requirements_text is not None:
                dst.writestr(_requirements.JAR_ENTRY, requirements_text.encode("utf-8"))
                print(f"[pymerlin] Bundled:         {_requirements.JAR_ENTRY}")

            if bundle_model:
                if is_package:
                    # Bundle the entire package directory
                    for py_file in model_sources:
                        arc_name = f"pymerlin_models/{pkg_dir.name}/{py_file.relative_to(pkg_dir)}"
                        dst.write(py_file, arc_name)
                    print(f"[pymerlin] Bundled package: {pkg_dir.name}/")
                else:
                    dst.write(model_file, f"pymerlin_models/{model_file.name}")
                    print(f"[pymerlin] Bundled:         {model_file.name}")

        shutil.copy(tmp_jar, output_jar)

    print(f"[pymerlin] Done. JAR written to: {output_jar}")


def _generate_requirements(
    model_file: Path,
    model_sources,
    pkg_dir: Path,
    is_package: bool,
    bundle_model: bool,
):
    """
    Return the requirements.txt text to bundle, or None if the model needs no packages.

    The model's imports are the requirements — there is no hand-written alternative, and a
    requirements.txt sitting next to the model is not read. Authors edit imports; packaging
    turns them into the file.
    """
    local_names = _local_module_names(pkg_dir, is_package)
    generated = _requirements.generate(
        model_sources, local_names, package_root=pkg_dir if is_package else None)

    _warn(_stranded_local_imports(generated, model_file, is_package, bundle_model))
    _warn(generated.warnings, prefix="generated requirements: ")

    if generated.provided:
        preinstalled = ", ".join(sorted(set(generated.provided)))
        print(f"[pymerlin] Preinstalled:    {preinstalled} (already in the worker image)")
    if generated.text is None:
        print("[pymerlin] Requirements:    none — the model imports nothing to install")
        return None

    print("[pymerlin] Requirements:    generated from the model's imports")
    for requirement in generated.requirements:
        print(f"[pymerlin]                  {requirement}")
    return generated.text


def _local_module_names(pkg_dir: Path, is_package: bool):
    """
    Top-level module names that resolve from the model's own directory.

    These are the model's own modules, not packages to install — importing a sibling
    `helpers.py` must not put "helpers" in the generated requirements.
    """
    names = set()
    if is_package:
        names.add(pkg_dir.name)
    for entry in pkg_dir.iterdir():
        if entry.suffix == ".py":
            names.add(entry.stem)
        elif entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
    return names


def _stranded_local_imports(generated, model_file: Path, is_package: bool, bundle_model: bool):
    """
    Warn about local modules a single-file model imports but that don't ship with it.

    Only a package (a directory with an `__init__.py`) is bundled whole; next to a bare
    model.py, a sibling `helpers.py` is left behind and the model fails to import on the
    worker. The scan makes this visible at package time, where it is a one-line fix.
    """
    if is_package or not bundle_model:
        return []
    directory = model_file.parent
    stranded = [
        name for name in generated.imports
        if name != model_file.stem
        and ((directory / f"{name}.py").is_file() or (directory / name / "__init__.py").is_file())
    ]
    if not stranded:
        return []
    warning = (
        f"{model_file.name} imports {', '.join(stranded)} from its own directory, but only "
        f"{model_file.name} is bundled — the import will fail on the worker. Make the model "
        "a package by adding an __init__.py next to it, and the whole directory ships"
    )
    return [warning]


def _warn(warnings, prefix: str = ""):
    if not warnings:
        return
    # Warnings go to stderr; flush stdout first so they don't jump ahead of the packaging
    # log they annotate when both streams are redirected to the same file.
    sys.stdout.flush()
    for warning in warnings:
        print(f"[pymerlin] WARNING: {prefix}{warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
