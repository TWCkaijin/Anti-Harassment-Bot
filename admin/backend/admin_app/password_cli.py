"""Bootstrap/reset a shared admin password using a secure terminal prompt."""

import argparse
import getpass
import os
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from admin_app import infrastructure
from admin_app.auth import credential_ref, password_hasher
from admin_app.config import get_admin_settings


def configure_password(password: str | None, *, reset=False, disable=False):
    if not disable and (
        not isinstance(password, str) or len(password) < 12 or len(password) > 1024
    ):
        raise ValueError("Password must contain between 12 and 1024 characters")
    password_hash = None if disable else password_hasher().hash(password)
    ref = credential_ref()

    def update(transaction):
        previous = ref.get(transaction=transaction).to_dict()
        if previous and not reset and not disable:
            raise ValueError("Admin credentials already exist; use --reset to rotate")
        version = int((previous or {}).get("auth_version", 0)) + 1
        document = {
            "auth_version": version,
            "enabled": not disable,
            "updated_at": datetime.now(UTC),
        }
        if password_hash is not None:
            document["password_hash"] = password_hash
        transaction.set(ref, document)
        return version

    return infrastructure.atomic(update)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["production", "preview"], required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--use-gcloud-login",
        action="store_true",
        help="Use the active gcloud CLI login instead of ADC; no key file is created",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reset", action="store_true")
    mode.add_argument("--disable", action="store_true")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--password-file", type=Path, help="Read a local owner-only password file")
    source.add_argument(
        "--generate-password-file",
        type=Path,
        help="Create a new owner-only file containing a random password",
    )
    args = parser.parse_args(argv)
    os.environ["ENVIRONMENT"] = args.environment
    os.environ["GOOGLE_CLOUD_PROJECT"] = args.project
    os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = args.project
    os.environ["ADMIN_PRIVATE_DATABASE_ID"] = (
        "admin-private" if args.environment == "production" else "admin-private-dev"
    )
    get_admin_settings.cache_clear()
    if args.use_gcloud_login:
        import firebase_admin
        from firebase_admin import credentials
        from google.oauth2.credentials import Credentials

        token = subprocess.run(
            ["gcloud", "auth", "print-access-token"], text=True, capture_output=True, check=False
        )
        if token.returncode:
            parser.error("The active gcloud login could not issue an access token")

        class OperatorCredential(credentials.Base):
            def get_credential(self):
                return Credentials(token.stdout.strip(), quota_project_id=args.project)

        if firebase_admin._apps:
            parser.error("Run the credential CLI in a fresh Python process")
        firebase_admin.initialize_app(OperatorCredential(), options={"projectId": args.project})
    password = None
    if not args.disable:
        if args.generate_password_file:
            path = args.generate_password_file
            path.parent.mkdir(parents=True, exist_ok=True)
            password = secrets.token_urlsafe(32)
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as stream:
                stream.write(password + "\n")
        elif args.password_file:
            if args.password_file.stat().st_mode & 0o077:
                parser.error("Password file must be readable only by its owner (chmod 600)")
            password = args.password_file.read_text().rstrip("\r\n")
        else:
            password = getpass.getpass("Admin password (at least 12 characters): ")
            if password != getpass.getpass("Repeat password: "):
                parser.error("Passwords do not match")
    try:
        version = configure_password(password, reset=args.reset, disable=args.disable)
    except ValueError as exc:
        parser.error(str(exc))
    print(
        f"Admin credentials updated for {args.environment}; authentication version {version}. Existing sessions are invalidated."
    )


if __name__ == "__main__":
    main()
