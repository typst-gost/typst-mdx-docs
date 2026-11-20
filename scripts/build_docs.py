#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from git import Repo
from loguru import logger

from scripts.parser.parse_docs import DEFAULT_OUTPUT_DIR as DEFAULT_MDX_DIR
from scripts.parser.parse_docs import parse_docs
from utils import RichCloneProgress, ensure_directories, run_process_with_progress

SCCACHE_PATH = shutil.which("sccache")
if SCCACHE_PATH:
    logger.success(f"Found sccache at {SCCACHE_PATH}, build acceleration enabled")
else:
    logger.warning("sccache not found, using default cargo")

DEFAULT_BUILD_DIR = Path("build")
DEFAULT_OUTPUT_DIR = DEFAULT_BUILD_DIR / "dist"
TYPST_REPO_URL = "https://github.com/typst/typst.git"


def get_pinned_rust_version(repo_dir: Path) -> str | None:
    ci_path = repo_dir / ".github" / "workflows" / "ci.yml"
    if ci_path.exists():
        try:
            content = ci_path.read_text(encoding="utf-8")
            match = re.search(r"rust-toolchain@(\d+\.\d+(?:\.\d+)?)", content) or \
                    re.search(r'toolchain:\s*"?(\d+\.\d+(?:\.\d+)?)"?', content)
            if match:
                logger.success(f"Found pinned Rust version from CI workflow: {match.group(1)}")
                return match.group(1)
        except Exception as e:
            logger.warning(f"Failed to parse Rust version from CI workflow: {e}")

    cargo_path = repo_dir / "Cargo.toml"
    if cargo_path.exists():
        try:
            content = cargo_path.read_text(encoding="utf-8")
            match = re.search(r'rust-version\s*=\s*"(\d+\.\d+(?:\.\d+)?)"', content)
            if match:
                logger.success(f"Found pinned Rust version from Cargo.toml: {match.group(1)}")
                return match.group(1)
        except Exception as e:
            logger.warning(f"Failed to parse Rust version from Cargo.toml: {e}")

    return None


def is_toolchain_installed(version: str) -> bool:
    if not version:
        return False
    try:
        result = subprocess.run(
            ["rustup", "toolchain", "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        return any(line.startswith(version) for line in result.stdout.splitlines())
    except subprocess.CalledProcessError:
        return False


def manage_toolchain(version: str, action: str):
    if not version or version == "stable":
        return

    cmd = ["rustup", "toolchain", action, version]
    if action == "install":
        cmd.extend(["--profile", "minimal", "--no-self-update"])

    logger.info(f"Rustup: {action} {version}...")
    try:
        subprocess.run(cmd, check=True)
        logger.success(f"Rust {version} {action}ed successfully")
    except subprocess.CalledProcessError:
        logger.error(f"Failed to {action} toolchain {version}")


def sync_typst_repo(typst_dir: Path, branch: str = "main") -> Repo:
    if typst_dir.exists():
        logger.info(f"Updating existing repo at {typst_dir}...")
        try:
            repo = Repo(typst_dir)
            repo.remotes.origin.fetch()
            repo.git.checkout("-B", branch, f"origin/{branch}")
            repo.git.clean("-fd")
            logger.success(f"Successfully synced with origin/{branch}")
            return repo
        except Exception as e:
            logger.error(f"Failed to update repo: {e}")
            exit(1)

    logger.info(f"Cloning {TYPST_REPO_URL} (branch: {branch})...")
    try:
        repo = Repo.clone_from(
            TYPST_REPO_URL,
            typst_dir,
            branch=branch,
            depth=1,
            progress=RichCloneProgress(),
        )
        logger.success("Clone completed")
        return repo
    except Exception as e:
        logger.error(f"Failed to clone {TYPST_REPO_URL}: {e}")
        exit(1)


def compile_docs(typst_dir: Path, extra_args: list[str] | None = None) -> bool:
    docs_dir = typst_dir / "docs"
    if not docs_dir.exists():
        logger.error(f"Documentation directory not found at {docs_dir}")
        return False

    cmd = ["cargo", "docit", "compile"]
    if extra_args:
        cmd.extend(extra_args)

    build_env = os.environ.copy()
    if SCCACHE_PATH:
        build_env["RUSTC_WRAPPER"] = SCCACHE_PATH

    logger.info(f"Building docs in {docs_dir} with System Rust...")
    return_code = run_process_with_progress(
        cmd,
        "Compiling Docs (System Rust)",
        cwd=docs_dir,
        env=build_env,
    )

    if return_code == 0:
        logger.success("Docs compiled successfully with System Rust")
        return True

    logger.warning("Build failed with system Rust, falling back to pinned version...")

    required_version = get_pinned_rust_version(typst_dir)
    if not required_version:
        logger.error("No pinned Rust version found, aborting")
        return False

    toolchain_installed = is_toolchain_installed(required_version)
    installed_by_us = False

    logger.info(f"Required Rust version: {required_version}")
    try:
        if not toolchain_installed:
            manage_toolchain(required_version, "install")
            installed_by_us = True

        specific_cmd = list(cmd)
        specific_cmd.insert(1, f"+{required_version}")

        return_code = run_process_with_progress(
            specific_cmd,
            f"Compiling Docs (Pinned Rust {required_version})",
            cwd=docs_dir,
            env=build_env,
        )
        return return_code == 0
    finally:
        if installed_by_us and required_version != "stable":
            manage_toolchain(required_version, "uninstall")


def export_artifacts(docs_dir: Path, output_dir: Path):
    source_dist = docs_dir / "dist"
    if not source_dist.exists():
        logger.error(f"Expected build artifacts at {source_dist}, but directory does not exist")
        exit(1)

    logger.info(f"Copying build artifacts from {source_dist} to {output_dir}...")
    if output_dir.exists():
        shutil.rmtree(output_dir)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dist, output_dir)
    logger.success(f"Documentation successfully built and placed in {output_dir.resolve()}")


def parse_args():
    parser = argparse.ArgumentParser(description="Typst Docs Builder (cargo docit)")
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=DEFAULT_BUILD_DIR,
        help="Build directory (contains typst repo)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Destination directory for compiled HTML docs (default: build/dist)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default="main",
        help="Typst Git branch to checkout (default: main)",
    )
    parser.add_argument(
        "--parse",
        action="store_true",
        help="Also convert compiled HTML to MDX into build/mdx",
    )
    parser.add_argument(
        "--mdx-dir",
        type=Path,
        default=DEFAULT_MDX_DIR,
        help="Destination directory for MDX when using --parse (default: build/mdx)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    ensure_directories([args.build_dir])
    typst_dir = args.build_dir / "typst"

    sync_typst_repo(typst_dir, branch=args.branch)

    success = compile_docs(typst_dir)
    if not success:
        logger.error("Documentation compilation failed")
        exit(1)

    export_artifacts(typst_dir / "docs", args.output_dir)

    if args.parse:
        code = parse_docs(source_dir=args.output_dir, output_dir=args.mdx_dir)
        exit(code)


if __name__ == "__main__":
    main()