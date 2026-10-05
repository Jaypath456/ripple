import argparse

from app.verification import load_latest_verification, verify_repository
from app.verification_render import render_verification_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="compare a prediction with a Git range")
    verify.add_argument("repo")
    return parser


def _run_verify(args: argparse.Namespace) -> int:
    run = verify_repository(args.repo)
    print(render_verification_markdown(run))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify":
        return _run_verify(args)
    return 1


def latest(repo: str) -> dict:
    return load_latest_verification(repo)
