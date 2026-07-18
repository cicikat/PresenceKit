#!/usr/bin/env python3
"""构建 PresenceKit 发行包（路线 A：uv 引导包，见 cc-tasks/92 §2/§4）。

CI（.github/workflows/release.yml）与本机跑同一份脚本，保证可复现。

用法：
    python scripts/build_release.py [--version v0.1.0] [--uv-version 0.11.29]

产出：dist/PresenceKit-<version>-win64-setup.zip 及同名 .sha256 校验文件。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"

# 随包携带的 uv 版本，固定以保证可复现；升级需同步改这里。
DEFAULT_UV_VERSION = "0.11.29"
UV_ASSET = "uv-x86_64-pc-windows-msvc.zip"
UV_RELEASE_BASE = "https://github.com/astral-sh/uv/releases/download"


def _git_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        short_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip()
        return f"dev-{short_sha}"


def _download_uv(uv_version: str, dest: Path) -> None:
    url = f"{UV_RELEASE_BASE}/{uv_version}/{UV_ASSET}"
    print(f"[build_release] 下载 uv {uv_version}: {url}")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / UV_ASSET
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        exe = next(Path(tmp).rglob("uv.exe"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(exe, dest)


def _stage_source(staging: Path) -> None:
    """导出仓库源码：git archive 只含已跟踪文件，天然等价于 .gitignore 过滤后的结果。"""
    print("[build_release] 导出仓库源码...")
    tar_bytes = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, check=True,
    ).stdout
    staging.mkdir(parents=True, exist_ok=True)
    tar_path = staging.parent / "source.tar"
    tar_path.write_bytes(tar_bytes)
    shutil.unpack_archive(str(tar_path), str(staging))
    tar_path.unlink()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(version: str, uv_version: str) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "PresenceKit"
        _stage_source(staging)
        _download_uv(uv_version, staging / "tools" / "uv.exe")

        zip_name = f"PresenceKit-{version}-win64-setup.zip"
        zip_path = DIST_DIR / zip_name
        if zip_path.exists():
            zip_path.unlink()

        print(f"[build_release] 打包: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in staging.rglob("*"):
                if file.is_file():
                    zf.write(file, file.relative_to(staging))

        sha_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
        digest = _sha256(zip_path)
        sha_path.write_text(f"{digest}  {zip_name}\n", encoding="utf-8")

        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"[build_release] 完成: {zip_path} ({size_mb:.1f} MB)")
        print(f"[build_release] SHA256: {digest}")
        return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="版本号（默认从 git tag 推断，否则用 dev-<sha>）")
    parser.add_argument("--uv-version", default=DEFAULT_UV_VERSION, help="随包携带的 uv 版本")
    args = parser.parse_args()

    version = args.version or _git_version()
    build(version, args.uv_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
