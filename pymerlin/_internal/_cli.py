"""
Command-line interface for PyMerlin.
"""

import argparse
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


_SHIM_JAR = Path(__file__).parent / "jars" / "pymerlin-shim.jar"


def main():
    parser = argparse.ArgumentParser(prog="pymerlin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pkg_parser = subparsers.add_parser(
        "package",
        help="Package a Python model into an Aerie-compatible mission model JAR"
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

    args = parser.parse_args()

    if args.command == "package":
        _package(args.model, args.out, bundle_model=args.bundle_model)


def _package(model_ref: str, output_jar: str, bundle_model: bool = True):
    if not _SHIM_JAR.exists():
        print(f"[pymerlin] ERROR: shim JAR not found at {_SHIM_JAR}", file=sys.stderr)
        sys.exit(1)

    if ":" not in model_ref:
        print(f"[pymerlin] ERROR: --model must be 'path/to/file.py:ClassName'", file=sys.stderr)
        sys.exit(1)

    model_file_path, class_name = model_ref.rsplit(":", 1)
    model_file = Path(model_file_path).resolve()

    if not model_file.exists():
        print(f"[pymerlin] ERROR: model file not found: {model_file}", file=sys.stderr)
        sys.exit(1)

    # The model ref stored in the JAR uses the bundled path if we bundle,
    # otherwise the absolute path on the host.
    if bundle_model:
        jar_model_ref = f"pymerlin_models/{model_file.name}:{class_name}"
    else:
        jar_model_ref = f"{model_file}:{class_name}"

    print(f"[pymerlin] Packaging model: {model_ref}")
    print(f"[pymerlin] Shim JAR:        {_SHIM_JAR}")
    print(f"[pymerlin] Output:          {output_jar}")

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
                    manifest += f"\r\nPymerlin-Model-Ref: {jar_model_ref}\r\n\r\n"
                    dst.writestr(item, manifest.encode("utf-8"))
                else:
                    dst.writestr(item, data)

            # Bundle the Python model file
            if bundle_model and model_file.exists():
                dst.write(model_file, f"pymerlin_models/{model_file.name}")
                print(f"[pymerlin] Bundled:         {model_file.name}")

        shutil.copy(tmp_jar, output_jar)

    print(f"[pymerlin] Done. JAR written to: {output_jar}")


if __name__ == "__main__":
    main()
