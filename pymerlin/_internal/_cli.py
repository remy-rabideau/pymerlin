"""
Command-line interface for PyMerlin.
"""

import argparse
import importlib.util
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="pymerlin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # build-jar command
    build_parser = subparsers.add_parser(
        "build-jar",
        help="Generate an Aerie-compatible mission model JAR from a Python model"
    )
    build_parser.add_argument(
        "--model",
        required=True,
        help="Path to model file and class name (e.g., ./model.py:Mission)"
    )
    build_parser.add_argument(
        "--name",
        required=True,
        help="Model name"
    )
    build_parser.add_argument(
        "--version",
        required=True,
        help="Model version"
    )
    build_parser.add_argument(
        "--out",
        required=True,
        help="Output JAR file path"
    )
    
    args = parser.parse_args()
    
    if args.command == "build-jar":
        model_class = _load_model_class(args.model)
        
        from pymerlin._internal._codegen import generate_mission_model_jar
        
        print(f"[pymerlin] Generating mission model JAR for {args.name}@{args.version}")
        generate_mission_model_jar(
            model_class,
            args.name,
            args.version,
            args.out,
            args.model  # Pass the model reference
        )
        print(f"[pymerlin] Wrote {args.out}")


def _load_model_class(model_ref: str):
    """Load a model class from 'path/to/file.py:ClassName'."""
    if ":" not in model_ref:
        raise ValueError(f"model_ref must be 'path/to/file.py:ClassName', got: {model_ref!r}")
    
    file_path, class_name = model_ref.rsplit(":", 1)
    file_path = os.path.abspath(file_path)
    
    spec = importlib.util.spec_from_file_location("_pymerlin_user_model", file_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(file_path))
    spec.loader.exec_module(module)
    
    return getattr(module, class_name)


if __name__ == "__main__":
    main()
