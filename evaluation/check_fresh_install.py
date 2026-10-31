"""Validate a fresh working-tree snapshot with a new venv and cached wheels.

This includes uncommitted source changes, excludes secrets/state, and neither
commits nor edits the user's repository. Requires a wheelhouse of the declared
dependencies (including setuptools) prepared separately with pip wheel.
"""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path
from time import perf_counter


def check(root: Path, wheels: Path) -> dict:
    destination = Path(tempfile.mkdtemp(prefix="ripple-fresh-install-"))
    snapshot = destination / "ripple"
    snapshot.mkdir()
    for name in ("src", "tests", "evaluation", ".github"):
        shutil.copytree(
            root / name,
            snapshot / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
    for name in ("README.md", "pyproject.toml", ".gitignore", ".env.example"):
        shutil.copy2(root / name, snapshot / name)
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("RIPPLE_LLM_", "OPENAI_"))
        and k not in {"PYTHONPATH", "RIPPLE_MAX_TOKENS"}
    }
    environment.update(
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(wheels.resolve()),
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    env_path = destination / "venv"
    venv.EnvBuilder(with_pip=True).create(env_path)
    python = env_path / "bin/python"
    results = []
    commands = [
        [str(python), "-m", "pip", "install", "-e", ".[dev]"],
        [str(python), "-m", "pip", "check"],
        [
            str(python),
            "-c",
            "import ripple; from ripple.llm import OpenAILLM; OpenAILLM(api_key='fixture-not-a-real-key', model='fixture', base_url='https://example.invalid/v1'); print(ripple.__file__)",
        ],
        [str(env_path / "bin/ruff"), "check", "."],
        [str(env_path / "bin/ruff"), "format", "--check", "."],
        [str(env_path / "bin/pytest"), "-q"],
        [str(env_path / "bin/ripple"), "demo", "--output", str(destination / "demos")],
    ]
    for command in commands:
        started = perf_counter()
        process = subprocess.run(
            command,
            cwd=snapshot,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            {
                "command": command,
                "returncode": process.returncode,
                "seconds": perf_counter() - started,
                "stdout": process.stdout,
                "stderr": process.stderr,
            }
        )
        if process.returncode:
            break
    return {
        "mode": "fresh snapshot of current uncommitted source, new venv, offline wheel install",
        "snapshot": str(snapshot),
        "wheelhouse": str(wheels.resolve()),
        "network_for_install": False,
        "all_passed": len(results) == len(commands)
        and all(r["returncode"] == 0 for r in results),
        "checks": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.root, args.wheels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))
    raise SystemExit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    main()
