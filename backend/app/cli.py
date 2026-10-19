"""Command line tasks.

    python -m app.cli create-user --email ada@example.com --name "Ada" --role admin

The password is asked for twice (or read from stdin with --password-stdin), so it never
appears in shell history or in code.
"""

import argparse
import getpass
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import TeamUser

MIN_PASSWORD_LENGTH = 12


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match.")
    return first


def create_user(email: str, name: str, role: str, password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
    engine = create_engine(get_settings().database_url)
    with Session(engine) as db, db.begin():
        email = email.strip().lower()
        if db.scalar(select(TeamUser.id).where(TeamUser.email == email)) is not None:
            raise SystemExit(f"A team user with email {email} already exists.")
        db.add(TeamUser(email=email, name=name, role=role, password_hash=hash_password(password)))
    engine.dispose()
    print(f"Created {role} {email}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Add a control room user.")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=["viewer", "agent", "admin"], required=True)
    create.add_argument(
        "--password-stdin", action="store_true", help="Read the password from stdin."
    )
    args = parser.parse_args(argv)
    if args.command == "create-user":
        create_user(args.email, args.name, args.role, _read_password(args.password_stdin))


if __name__ == "__main__":
    main()
