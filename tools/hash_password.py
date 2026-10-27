#!/usr/bin/env python3
"""Generate a database-ready password hash using Food Truck Works auth.py."""

import argparse
import getpass
from pathlib import Path
import sys
import warnings


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        from auth import hash_password, validate_password, verify_password
    except ImportError:
        print("Cannot load auth.py. Place this script in the project's tools directory "
              "and run it with the project's Python environment.", file=sys.stderr)
        return 1

    try:
        # Fail closed if getpass cannot hide input rather than echoing a password.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("New password: ")
            error = validate_password(password)
            if error:
                print(error, file=sys.stderr)
                return 1
            confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match. No hash generated.", file=sys.stderr)
            return 1
        password_hash = hash_password(password)
        if not verify_password(password, password_hash):
            print("Hash verification failed. No hash emitted.", file=sys.stderr)
            return 1
    except getpass.GetPassWarning:
        print("Hidden password input is unavailable. Run in an interactive terminal.",
              file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No hash generated.", file=sys.stderr)
        return 130

    print(password_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
