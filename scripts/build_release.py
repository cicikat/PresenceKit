#!/usr/bin/env python3
"""构建 PresenceKit 发行包（路线 A：uv 引导包，见 cc-tasks/92 §2/§4）。

CI（.github/workflows/release.yml）与本机跑同一份脚本，保证可复现。

用法：
    python scripts/build_release.py --version v1.1.0
    python scripts/build_release.py --version v1.1.0 --platform win64
    python scripts/build_release.py --version v1.1.0 --all-platforms

产出：
    dist/PresenceKit-<version>-win64-setup.zip
    dist/PresenceKit-<version>-macos-arm64-setup.zip
    dist/PresenceKit-<version>-macos-x64-setup.zip
    以及各自的 .sha256。
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# Windows CI 跑者默认非 UTF-8 控制台代码页（如 cp1252），print() 中文日志
# 会直接 UnicodeEncodeError 崩掉整个构建；本机中文 Windows 不会暴露这个问题。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"

# 随包携带的 uv 版本，固定以保证可复现；升级需同步改这里。
DEFAULT_UV_VERSION = "0.11.29"
UV_RELEASE_BASE = "https://github.com/astral-sh/uv/releases/download"

PLATFORMS: dict[str, dict[str, str]] = {
    "win64": {
        "uv_asset": "uv-x86_64-pc-windows-msvc.zip",
        "uv_name": "uv.exe",
        "zip_suffix": "win64-setup",
    },
    "macos-arm64": {
        "uv_asset": "uv-aarch64-apple-darwin.tar.gz",
        "uv_name": "uv",
        "zip_suffix": "macos-arm64-setup",
    },
    "macos-x64": {
        "uv_asset": "uv-x86_64-apple-darwin.tar.gz",
        "uv_name": "uv",
        "zip_suffix": "macos-x64-setup",
    },
}


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


def _host_platform() -> str:
    if sys.platform == "darwin":
        import platform
        machine = platform.machine().lower()
        return "macos-arm64" if machine in {"arm64", "aarch64"} else "macos-x64"
    return "win64"


def _download_uv(uv_version: str, dest: Path, uv_asset: str, uv_name: str) -> None:
    url = f"{UV_RELEASE_BASE}/{uv_version}/{uv_asset}"
    print(f"[build_release] 下载 uv {uv_version}: {url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = Path(tmp) / uv_asset
        urllib.request.urlretrieve(url, archive_path)
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir()
        if uv_asset.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)
        else:
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_dir)
        binary = next(path for path in extract_dir.rglob(uv_name) if path.is_file())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, dest)


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


def build_platform(version: str, uv_version: str, platform_name: str) -> Path:
    spec = PLATFORMS[platform_name]
    DIST_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "PresenceKit"
        _stage_source(staging)
        _download_uv(
            uv_version,
            staging / "tools" / spec["uv_name"],
            spec["uv_asset"],
            spec["uv_name"],
        )
        # Release 包没有 .git，更新器靠这个标记展示当前版本并判断降级。
        (staging / "VERSION").write_text(f"{version}\n", encoding="utf-8")

        zip_name = f"PresenceKit-{version}-{spec['zip_suffix']}.zip"
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


def build(version: str, uv_version: str, platforms: list[str]) -> list[Path]:
    return [build_platform(version, uv_version, name) for name in platforms]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None, help="版本号（默认从 git tag 推断，否则用 dev-<sha>）")
    parser.add_argument("--uv-version", default=DEFAULT_UV_VERSION, help="随包携带的 uv 版本")
    parser.add_argument(
        "--platform",
        action="append",
        choices=sorted(PLATFORMS),
        help="只打指定平台；可重复。默认当前主机平台。",
    )
    parser.add_argument(
        "--all-platforms",
        action="store_true",
        help="同时打 win64、macos-arm64、macos-x64",
    )
    args = parser.parse_args()

    version = args.version or _git_version()
    if args.all_platforms:
        platforms = ["win64", "macos-arm64", "macos-x64"]
    elif args.platform:
        platforms = list(dict.fromkeys(args.platform))
    else:
        platforms = [_host_platform()]
    build(version, args.uv_version, platforms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
