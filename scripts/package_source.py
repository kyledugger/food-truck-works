#!/usr/bin/env python3

"""
package_source.py

Create a ZIP containing the source files of a Python project.

Features:
    - Uses .gitignore rules
    - Excludes common Python/project fluff
    - Preserves project directory structure
    - Places the ZIP in the project root
    - Automatically increments the ZIP filename if it already exists
    - Never includes previous output ZIPs
    - Supports additional custom exclusions
    - Does not require third-party packages

Usage:

    python package_source.py

Or:

    python package_source.py C:/dev/my_project

Optional arguments:

    python package_source.py --output source.zip
    python package_source.py --verbose
    python package_source.py --include-tests
    python package_source.py --root C:/dev/my_project

The default behavior is intended to create a clean source snapshot
suitable for giving to another developer, uploading for review,
or providing to an AI/code-analysis tool.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import zipfile


# ============================================================================
# CONFIGURATION
# ============================================================================

# Files that are always excluded regardless of .gitignore.
DEFAULT_EXCLUDED_FILES = {
    # Python project metadata / caches
    ".DS_Store",
    "Thumbs.db",

    # Packaging output
    "source_package.zip",

    # Environment/config files that commonly contain secrets
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",

    # Python tooling
    "pip-wheel-metadata",
}


# Directories that are always excluded.
DEFAULT_EXCLUDED_DIRS = {
    # Git
    ".git",

    # Python caches
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",

    # Virtual environments
    ".venv",
    "venv",
    "env",
    "ENV",

    # Python build/package artifacts
    "build",
    "dist",
    "*.egg-info",

    # Coverage
    ".coverage",
    "htmlcov",

    # IDEs/editors
    ".idea",
    ".vscode",

    # Node/frontend dependencies
    "node_modules",

    # OS metadata
    ".Trash",
}


# File extensions that are generally not source code.
# Remove anything here if your project legitimately needs it.
DEFAULT_EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",

    # Compiled/native binaries
    ".so",
    ".dll",
    ".dylib",

    # Python wheels / distributions
    ".whl",

    # Archives
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".rar",

    # Large/media files
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".wav",

    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",

    # Office/document files
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",

    # Database files
    ".db",
    ".sqlite",
    ".sqlite3",

    # Logs
    ".log",
}


# Additional project-specific exclusions.
#
# These use simple glob matching against the relative path.
#
# Examples:
#
#     "data/*"
#     "uploads/*"
#     "secrets/*"
#     "*.json"
#
# NOTE:
# Do NOT put normal source directories here unless you intentionally
# want them omitted.
CUSTOM_EXCLUDED_PATTERNS = {
    # Examples:
    # "data/*",
    # "uploads/*",
    # "secrets/*",
    # "*.secret",
}


# ============================================================================
# .gitignore SUPPORT
# ============================================================================

def load_gitignore(root: Path) -> list[str]:
    """
    Load .gitignore rules from the project root.

    This intentionally implements the common/simple .gitignore behavior
    needed for source packaging without requiring the 'pathspec' package.

    For extremely complex .gitignore files, installing 'pathspec' and using
    Git's own ignore engine would be more exact.
    """

    gitignore = root / ".gitignore"

    if not gitignore.exists():
        return []

    rules = []

    try:
        text = gitignore.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = gitignore.read_text(encoding="utf-8", errors="replace")

    for line in text.splitlines():
        line = line.strip()

        # Blank lines
        if not line:
            continue

        # Comments
        if line.startswith("#"):
            continue

        rules.append(line)

    return rules


def gitignore_match(relative_path: str, is_dir: bool, rules: list[str]) -> bool:
    """
    Basic .gitignore-style matching.

    Supports:
        *.pyc
        __pycache__/
        secrets/
        /config.json
        data/*
        !important.py

    Returns True if the path should be ignored.
    """

    path = relative_path.replace("\\", "/").strip("/")

    ignored = False

    for rule in rules:
        rule = rule.strip()

        if not rule or rule.startswith("#"):
            continue

        # Negation rule
        negated = rule.startswith("!")

        if negated:
            rule = rule[1:]

        rule = rule.strip()

        if not rule:
            continue

        # Remove trailing slash for matching.
        directory_rule = rule.endswith("/")
        if directory_rule:
            rule = rule.rstrip("/")

        # Remove leading slash for normalized matching.
        anchored = rule.startswith("/")
        if anchored:
            rule = rule.lstrip("/")

        matched = False

        if anchored:
            matched = fnmatch.fnmatch(path, rule)
        else:
            # Match the entire relative path.
            if fnmatch.fnmatch(path, rule):
                matched = True

            # Match against each path component.
            parts = path.split("/")

            if any(fnmatch.fnmatch(part, rule) for part in parts):
                matched = True

            # Also support patterns containing '/'.
            if "/" in rule:
                matched = fnmatch.fnmatch(path, rule)

        if matched:
            if negated:
                ignored = False
            else:
                ignored = True

    return ignored


# ============================================================================
# EXCLUSION LOGIC
# ============================================================================

def matches_custom_exclusion(relative_path: str) -> bool:
    """
    Check the user's custom exclusion patterns.
    """

    path = relative_path.replace("\\", "/")

    for pattern in CUSTOM_EXCLUDED_PATTERNS:
        pattern = pattern.replace("\\", "/")

        if fnmatch.fnmatch(path, pattern):
            return True

        # Also allow a directory/file name pattern to match a component.
        if "/" not in pattern:
            if any(fnmatch.fnmatch(part, pattern) for part in path.split("/")):
                return True

    return False


def should_exclude(
    path: Path,
    root: Path,
    gitignore_rules: list[str],
    output_zip: Path,
) -> tuple[bool, str]:
    """
    Determine whether a file or directory should be excluded.

    Returns:
        (True, reason)
        (False, "")
    """

    relative = path.relative_to(root)
    relative_str = relative.as_posix()

    # ---------------------------------------------------------------------
    # Never package the output ZIP itself.
    # ---------------------------------------------------------------------

    if path.resolve() == output_zip.resolve():
        return True, "output ZIP"

    # ---------------------------------------------------------------------
    # Directory exclusions
    # ---------------------------------------------------------------------

    if path.is_dir():

        if path.name in DEFAULT_EXCLUDED_DIRS:
            return True, "excluded directory"

        if any(
            fnmatch.fnmatch(path.name, pattern)
            for pattern in DEFAULT_EXCLUDED_DIRS
            if "*" in pattern
        ):
            return True, "excluded directory pattern"

    # ---------------------------------------------------------------------
    # File exclusions
    # ---------------------------------------------------------------------

    if path.is_file():

        if path.name in DEFAULT_EXCLUDED_FILES:
            return True, "excluded filename"

        if path.suffix.lower() in DEFAULT_EXCLUDED_EXTENSIONS:
            return True, "excluded extension"

    # ---------------------------------------------------------------------
    # Custom exclusions
    # ---------------------------------------------------------------------

    if matches_custom_exclusion(relative_str):
        return True, "custom exclusion"

    # ---------------------------------------------------------------------
    # .gitignore
    # ---------------------------------------------------------------------

    if gitignore_match(
        relative_str,
        path.is_dir(),
        gitignore_rules,
    ):
        return True, ".gitignore"

    return False, ""


# ============================================================================
# OUTPUT NAME
# ============================================================================

def get_output_path(root: Path, requested_name: str) -> Path:
    """
    Find a non-conflicting output filename.

    Example:

        source_package.zip
        source_package_1.zip
        source_package_2.zip
        ...
    """

    requested = Path(requested_name)

    if requested.suffix.lower() != ".zip":
        requested = requested.with_suffix(".zip")

    base_name = requested.stem
    suffix = requested.suffix

    candidate = root / requested

    if not candidate.exists():
        return candidate

    counter = 1

    while True:
        candidate = root / f"{base_name}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


# ============================================================================
# SOURCE COLLECTION
# ============================================================================

def collect_files(
    root: Path,
    gitignore_rules: list[str],
    output_zip: Path,
    verbose: bool = False,
) -> list[Path]:
    """
    Walk the project and return files that should be included.
    """

    included = []

    for current_root, dirs, files in os.walk(root):

        current_path = Path(current_root)

        # -------------------------------------------------------------
        # Filter directories in-place.
        #
        # This is important because it prevents os.walk from descending
        # into directories we already know we don't want.
        # -------------------------------------------------------------

        remaining_dirs = []

        for dirname in dirs:
            directory = current_path / dirname

            excluded, reason = should_exclude(
                directory,
                root,
                gitignore_rules,
                output_zip,
            )

            if excluded:
                if verbose:
                    print(f"SKIP DIR : {directory.relative_to(root)} [{reason}]")
            else:
                remaining_dirs.append(dirname)

        dirs[:] = remaining_dirs

        # -------------------------------------------------------------
        # Process files.
        # -------------------------------------------------------------

        for filename in files:
            file_path = current_path / filename

            excluded, reason = should_exclude(
                file_path,
                root,
                gitignore_rules,
                output_zip,
            )

            if excluded:
                if verbose:
                    print(f"SKIP FILE: {file_path.relative_to(root)} [{reason}]")
                continue

            included.append(file_path)

    return included


# ============================================================================
# ZIP CREATION
# ============================================================================

def create_zip(
    root: Path,
    output_zip: Path,
    files: list[Path],
) -> None:
    """
    Create the ZIP while preserving the project directory structure.
    """

    with zipfile.ZipFile(
        output_zip,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:

        for file_path in files:

            # The path inside the ZIP is relative to the project root.
            archive_name = file_path.relative_to(root).as_posix()

            archive.write(
                file_path,
                arcname=archive_name,
            )


# ============================================================================
# COMMAND LINE
# ============================================================================

def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Package Python project source files into a ZIP."
    )

    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Project root directory. Defaults to the current directory.",
    )

    parser.add_argument(
        "--output",
        default="source_package.zip",
        help="Base output ZIP filename.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show included and excluded files.",
    )

    return parser.parse_args()


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:

    args = parse_args()

    root = Path(args.root).resolve()

    if not root.exists():
        raise SystemExit(f"ERROR: Project root does not exist: {root}")

    if not root.is_dir():
        raise SystemExit(f"ERROR: Project root is not a directory: {root}")

    # ---------------------------------------------------------------------
    # Load .gitignore.
    # ---------------------------------------------------------------------

    gitignore_rules = load_gitignore(root)

    # ---------------------------------------------------------------------
    # Pick a unique output name BEFORE scanning the project.
    #
    # This allows the exclusion logic to specifically recognize the
    # output ZIP.
    # ---------------------------------------------------------------------

    output_zip = get_output_path(
        root,
        args.output,
    )

    print()
    print("Python Source Packager")
    print("=" * 60)
    print(f"Project root : {root}")
    print(f".gitignore   : {'YES' if gitignore_rules else 'NO'}")
    print(f"Output       : {output_zip.name}")
    print()

    # ---------------------------------------------------------------------
    # Collect source files.
    # ---------------------------------------------------------------------

    files = collect_files(
        root=root,
        gitignore_rules=gitignore_rules,
        output_zip=output_zip,
        verbose=args.verbose,
    )

    if not files:
        raise SystemExit(
            "ERROR: No files were found to package."
        )

    # ---------------------------------------------------------------------
    # Create ZIP.
    # ---------------------------------------------------------------------

    create_zip(
        root=root,
        output_zip=output_zip,
        files=files,
    )

    # ---------------------------------------------------------------------
    # Report.
    # ---------------------------------------------------------------------

    size_mb = output_zip.stat().st_size / (1024 * 1024)

    print(f"Files included: {len(files)}")
    print(f"ZIP size      : {size_mb:.2f} MB")
    print()
    print(f"Created: {output_zip}")
    print()


if __name__ == "__main__":
    main()
