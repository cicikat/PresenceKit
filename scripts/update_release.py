#!/usr/bin/env python3
"""Update an unpacked PresenceKit release package without replacing user data.

The batch entry point deliberately stays tiny: a running ``.bat`` cannot safely
replace itself.  This program downloads and verifies an entire release before it
touches the installed program files, and it never overwrites private state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


RELEASES_URL = "https://api.github.com/repos/cicikat/PresenceKit/releases?per_page=100"
ASSET_RE = re.compile(r"^PresenceKit-(.+)-(win64|macos-arm64|macos-x64)-setup\.zip$")
PROTECTED_ROOTS = frozenset({"data", "userdata", ".venv"})
PROTECTED_FILES = frozenset({"config.yaml", "secrets.local.yaml"})
PROTECTED_PATHS = frozenset({
    PurePosixPath("tools/uv.exe"),
    PurePosixPath("tools/uv"),
})
BACKUP_MANIFEST_NAME = "_update_backup_manifest.json"
BACKUP_MANIFEST_SCHEMA_VERSION = 1

V1_BASELINE_VERSION = "v1.0.0"
SUPPORTED_DATA_LAYOUT_SCHEMA_VERSION = 1


class UpdateError(RuntimeError):
    """A user-actionable update failure; the current installation is retained."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_protected_relative_path(path: Path) -> bool:
    """Return whether a release path must never overwrite a local installation path."""
    normalized = PurePosixPath(*(part.lower() for part in path.parts))
    if not normalized.parts:
        return False
    if normalized.parts[0].lower() in PROTECTED_ROOTS:
        return True
    if len(normalized.parts) == 1 and normalized.name.lower() in PROTECTED_FILES:
        return True
    return normalized in PROTECTED_PATHS


def current_version(root: Path) -> str:
    marker = root / "VERSION"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip()
        if value:
            return value
    return "未知旧版"


def _version_key(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", value.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _backup_path(root: Path, version: str) -> Path:
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "_", version) or "unknown"
    return root / f"_update_backup_{safe_version}"


def _read_source_layout_schema(installation: Path) -> int:
    marker = installation / "data" / "layout_version.json"
    if not marker.is_file():
        raise UpdateError(
            "当前安装缺少 data/layout_version.json，无法确认 v1 数据布局；"
            "请按升级恢复文档备份后全新安装或恢复备份。"
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("无法读取 data/layout_version.json；请恢复备份或按文档手动迁移。") from exc
    if not isinstance(payload, dict) or payload.get("product_baseline") != "v1":
        raise UpdateError("当前数据不是受支持的 v1 baseline；请按文档手动迁移或恢复备份。")
    schema = payload.get("data_layout_schema_version")
    if not isinstance(schema, int):
        raise UpdateError("data/layout_version.json 缺少有效 schema；请恢复备份或按文档手动迁移。")
    return schema


def validate_v1_update(installation: Path, source_version: str, target_version: str) -> None:
    """Fail before writes unless this is a compatible v1 forward update."""
    source_key, target_key = _version_key(source_version), _version_key(target_version)
    if source_key is None:
        raise UpdateError("无法识别当前安装版本；仅支持 v1+ 连续更新。请备份后全新安装或恢复备份。")
    if source_key < _version_key(V1_BASELINE_VERSION):
        raise UpdateError("v0.x 为 preview，自动升级不受支持；请备份后全新安装 v1。")
    if target_key is None or target_key < _version_key(V1_BASELINE_VERSION):
        raise UpdateError("v1 不支持更新到 pre-v1 版本；请恢复升级前备份。")
    if target_key < source_key:
        raise UpdateError("v1 不支持 downgrade；请恢复升级前备份。")

    schema = _read_source_layout_schema(installation)
    if schema > SUPPORTED_DATA_LAYOUT_SCHEMA_VERSION:
        raise UpdateError("当前数据 schema 高于此程序支持范围；请使用匹配版本或恢复备份。")
    if schema != SUPPORTED_DATA_LAYOUT_SCHEMA_VERSION:
        raise UpdateError("当前数据 schema 不在此程序支持范围；请按文档手动迁移或恢复备份。")


def _read_proxy_config(root: Path) -> dict[str, str]:
    """Read the same proxy fields managed by the admin /proxy control surface."""
    config = root / "config.yaml"
    if not config.is_file():
        return {}
    try:
        import yaml  # Present in a normally installed release virtualenv.

        parsed = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        proxy = parsed.get("proxy", {})
        if not isinstance(proxy, dict) or not proxy.get("enabled", False):
            return {}
        result = {}
        for scheme, key in (("http", "http"), ("https", "https")):
            value = str(proxy.get(key, "")).strip()
            if value:
                result[scheme] = value
        return result
    except Exception as exc:
        print(f"[update] 无法读取代理配置，将直连下载：{exc}")
        return {}


def _opener(root: Path) -> urllib.request.OpenerDirector:
    proxies = _read_proxy_config(root)
    # Do not let an unrelated shell HTTP_PROXY setting quietly change the update
    # route.  The admin-controlled config is the source of truth.
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))


def download(url: str, destination: Path, opener: urllib.request.OpenerDirector) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with opener.open(request, timeout=45) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (urllib.error.URLError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"下载失败：{url}\n{exc}") from exc
    os.replace(temporary, destination)


def fetch_releases(root: Path) -> list[dict[str, Any]]:
    opener = _opener(root)
    temporary = root / "_update_releases.json"
    try:
        download(RELEASES_URL, temporary, opener)
        payload = json.loads(temporary.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UpdateError) as exc:
        raise UpdateError(
            "无法从 GitHub 获取版本列表。请检查网络或 config.yaml 的代理设置；"
            "也可以手动下载 release zip 后只覆盖程序文件。\n" + str(exc)
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    if not isinstance(payload, list):
        raise UpdateError("GitHub 返回的版本列表格式无效，未修改当前安装。")
    return [release for release in payload if isinstance(release, dict) and not release.get("draft")]


def parse_release_choice(value: str, releases: list[dict[str, Any]]) -> int:
    choice = value.strip()
    if not choice:
        return 0
    try:
        index = int(choice) - 1
    except ValueError as exc:
        raise UpdateError("请输入版本前的数字，或直接回车选择最新版。") from exc
    if not 0 <= index < len(releases):
        raise UpdateError("版本编号超出范围。")
    return index


def current_package_suffix() -> str:
    """Pick the GitHub asset suffix that matches this installation's host."""
    if sys.platform == "darwin":
        import platform
        machine = platform.machine().lower()
        return "macos-arm64-setup" if machine in {"arm64", "aarch64"} else "macos-x64-setup"
    return "win64-setup"


def _release_assets(release: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError(f"{release.get('tag_name', '所选版本')} 没有可下载资产。")
    suffix = current_package_suffix()
    preferred = re.compile(rf"^PresenceKit-.+-{re.escape(suffix)}\.zip$")
    zip_asset = next(
        (asset for asset in assets if isinstance(asset, dict) and preferred.match(str(asset.get("name", "")))),
        None,
    )
    if zip_asset is None and suffix == "win64-setup":
        # v1.0.x releases only published the Windows zip.
        zip_asset = next(
            (asset for asset in assets if isinstance(asset, dict) and ASSET_RE.match(str(asset.get("name", "")))),
            None,
        )
    if zip_asset is None:
        raise UpdateError(f"所选版本没有 PresenceKit {suffix} 安装 zip。")
    checksum_name = f"{zip_asset['name']}.sha256"
    checksum_asset = next((asset for asset in assets if isinstance(asset, dict) and asset.get("name") == checksum_name), None)
    if checksum_asset is None:
        raise UpdateError(f"所选版本缺少校验文件 {checksum_name}，为安全起见不会更新。")
    return zip_asset, checksum_asset


def verify_sha256(archive: Path, checksum_file: Path) -> None:
    match = re.search(r"\b([a-fA-F0-9]{64})\b", checksum_file.read_text(encoding="utf-8-sig", errors="replace"))
    if not match:
        raise UpdateError("SHA256 校验文件格式无效，未修改当前安装。")
    actual = sha256_file(archive)
    if actual.lower() != match.group(1).lower():
        raise UpdateError("SHA256 校验失败，下载文件可能不完整或被篡改；未修改当前安装。")


def extract_release(archive: Path, destination: Path) -> Path:
    try:
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                relative = PurePosixPath(entry.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise UpdateError("发行包包含不安全路径，未修改当前安装。")
            package.extractall(destination)
    except zipfile.BadZipFile as exc:
        raise UpdateError("下载的 release zip 无法解压，未修改当前安装。") from exc

    # Current packages are flat.  Accept one enclosing directory as a future
    # packaging-compatible layout, but reject arbitrary/missing payloads.
    children = [child for child in destination.iterdir() if child.name not in {"__MACOSX"}]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    if not (destination / "scripts" / "update_release.py").is_file():
        raise UpdateError("发行包缺少更新器，未修改当前安装。")
    return destination


def _copy_program_files(source: Path, installation: Path, backup: Path) -> list[tuple[Path, bool]]:
    replaced: list[tuple[Path, bool]] = []
    try:
        for candidate in sorted(source.rglob("*")):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(source)
            if is_protected_relative_path(relative):
                continue
            target = installation / relative
            had_original = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            # copy2 writes the fully staged file to a temporary sibling and swaps it
            # in, so interruption cannot leave a partially written program file.
            temporary = target.with_suffix(target.suffix + ".update-new")
            try:
                shutil.copy2(candidate, temporary)
                os.replace(temporary, target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            replaced.append((target, had_original))
        _remove_stale_bundled_files(source, installation, replaced)
    except Exception:
        # Keep this local list available for a mid-copy failure.  Returning a
        # list only on success would otherwise lose the already replaced paths.
        _rollback_program_files(installation, backup, replaced)
        raise
    return replaced


def _remove_stale_bundled_files(
    source: Path, installation: Path, replaced: list[tuple[Path, bool]],
) -> None:
    """Make the release-owned bundled tree match the verified target package.

    ``bundled/`` is program content, never a protected user root.  Keeping a
    source-only file there would make an update an incomplete release replace.
    Removed files join ``replaced`` so the existing copy-failure rollback
    restores them from the complete pre-update snapshot.
    """
    source_bundled = source / "bundled"
    installed_bundled = installation / "bundled"
    if not source_bundled.is_dir() or not installed_bundled.is_dir():
        return
    for candidate in sorted(
        installed_bundled.rglob("*"), key=lambda path: (len(path.parts), str(path)), reverse=True,
    ):
        if candidate.is_file():
            relative = candidate.relative_to(installed_bundled)
            if not (source_bundled / relative).is_file():
                candidate.unlink()
                replaced.append((candidate, True))
        elif candidate.is_dir() and not any(candidate.iterdir()):
            candidate.rmdir()


def _rollback_program_files(installation: Path, backup: Path, replaced: list[tuple[Path, bool]]) -> None:
    for target, had_original in reversed(replaced):
        relative = target.relative_to(installation)
        if had_original:
            original = backup / relative
            if original.exists():
                temporary = target.with_suffix(target.suffix + ".update-rollback")
                shutil.copy2(original, temporary)
                os.replace(temporary, target)
        elif target.exists():
            target.unlink()


def _prune_old_backups(root: Path, keep: Path) -> None:
    for candidate in root.glob("_update_backup_*"):
        if candidate != keep and candidate.is_dir():
            shutil.rmtree(candidate)


def _write_backup_manifest(backup: Path, source_version: str) -> None:
    """Record the complete restorable program snapshot before replacement.

    Private roots are intentionally absent: restore never writes them, and
    hashing a virtualenv would make every update unnecessarily expensive.
    """
    files: dict[str, str] = {}
    for candidate in sorted(backup.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(backup)
        if relative.name == BACKUP_MANIFEST_NAME or is_protected_relative_path(relative):
            continue
        files[relative.as_posix()] = sha256_file(candidate)
    payload = {
        "schema_version": BACKUP_MANIFEST_SCHEMA_VERSION,
        "source_version": source_version,
        "files": files,
    }
    manifest = backup / BACKUP_MANIFEST_NAME
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)


def _validate_backup_manifest(backup: Path) -> None:
    """Refuse an incomplete or tampered updater backup before restore writes."""
    manifest = backup / BACKUP_MANIFEST_NAME
    if not manifest.is_file():
        raise UpdateError("升级前备份缺少完整性 manifest，拒绝恢复以免覆盖当前安装。")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        files = payload["files"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise UpdateError("升级前备份 manifest 无法读取，拒绝恢复以免覆盖当前安装。") from exc
    if payload.get("schema_version") != BACKUP_MANIFEST_SCHEMA_VERSION or not isinstance(files, dict):
        raise UpdateError("升级前备份 manifest 格式不受支持，拒绝恢复以免覆盖当前安装。")

    expected: set[str] = set()
    for name, expected_digest in files.items():
        relative = PurePosixPath(name) if isinstance(name, str) else PurePosixPath()
        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or is_protected_relative_path(Path(*relative.parts))
            or not isinstance(expected_digest, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_digest)
        ):
            raise UpdateError("升级前备份 manifest 包含无效条目，拒绝恢复以免覆盖当前安装。")
        candidate = backup.joinpath(*relative.parts)
        if not candidate.is_file() or sha256_file(candidate).lower() != expected_digest.lower():
            raise UpdateError("升级前备份不完整或已损坏，拒绝恢复以免覆盖当前安装。")
        expected.add(relative.as_posix())

    actual = {
        candidate.relative_to(backup).as_posix()
        for candidate in backup.rglob("*")
        if candidate.is_file()
        and candidate.name != BACKUP_MANIFEST_NAME
        and not is_protected_relative_path(candidate.relative_to(backup))
    }
    if actual != expected:
        raise UpdateError("升级前备份内容与 manifest 不一致，拒绝恢复以免覆盖当前安装。")


def _create_installation_backup(installation: Path, backup: Path, source_version: str) -> None:
    """Create a complete pre-update snapshot before changing any installation file."""
    try:
        backup.mkdir(parents=True)
        for candidate in sorted(installation.rglob("*")):
            relative = candidate.relative_to(installation)
            # A backup lives under the installation.  Never recursively copy it,
            # previous backups, or the verified release staging area into itself.
            if relative.parts and (
                relative.parts[0].startswith("_update_backup_") or relative.parts[0] == "_update_tmp"
            ):
                continue
            destination = backup / relative
            if candidate.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif candidate.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination)
        _write_backup_manifest(backup, source_version)
    except Exception as exc:
        shutil.rmtree(backup, ignore_errors=True)
        raise UpdateError(f"升级前备份失败，当前安装未开始更新：{exc}") from exc


def apply_release(
    installation: Path, source: Path, old_version: str, *, reuse_existing_backup: bool = False,
) -> Path:
    """Overlay verified program files and retain one restorable pre-update backup."""
    existing_backups = sorted(path for path in installation.glob("_update_backup_*") if path.is_dir())
    if reuse_existing_backup and existing_backups:
        # A same-version retry must not turn the already-successful update into
        # a new snapshot of itself. The updater retains one backup by contract.
        backup = existing_backups[-1]
    else:
        backup = _backup_path(installation, old_version)
        if backup.exists():
            shutil.rmtree(backup)
        _create_installation_backup(installation, backup, old_version)
    replaced: list[tuple[Path, bool]] = []
    try:
        replaced = _copy_program_files(source, installation, backup)
    except Exception as exc:
        _rollback_program_files(installation, backup, replaced)
        raise UpdateError(
            f"程序文件复制失败，已恢复本次已替换的程序文件；"
            f"完整升级前备份保留在 {backup.name}，如需恢复请使用 --restore-backup：{exc}"
        ) from exc
    _prune_old_backups(installation, backup)
    return backup


def restore_installation_from_backup(installation: Path, backup: Path) -> None:
    """Explicitly restore a complete pre-update backup; this is the downgrade path."""
    resolved_installation = installation.resolve()
    resolved_backup = backup.resolve()
    try:
        relative_backup = resolved_backup.relative_to(resolved_installation)
    except ValueError as exc:
        raise UpdateError("恢复备份必须位于当前安装目录内。") from exc
    if not relative_backup.parts or not relative_backup.parts[0].startswith("_update_backup_"):
        raise UpdateError("恢复目标不是 updater 创建的升级前备份。")
    if not resolved_backup.is_dir():
        raise UpdateError("指定的升级前备份不存在。")
    _validate_backup_manifest(resolved_backup)
    # A failed update deliberately retains its verified staging area for
    # diagnosis.  A deliberate restore is also an explicit safe-cleanup point.
    shutil.rmtree(resolved_installation / "_update_tmp", ignore_errors=True)

    for candidate in sorted(resolved_installation.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        relative = candidate.relative_to(resolved_installation)
        if relative.parts and relative.parts[0].startswith("_update_backup_"):
            continue
        if relative.parts and relative.parts[0] == "_update_tmp":
            continue
        if relative.as_posix() == BACKUP_MANIFEST_NAME:
            continue
        # Updates never touch protected local state.  Skipping it on restore is
        # both contract-symmetric and necessary on Windows: this command is
        # normally running from .venv\Scripts\python.exe, which cannot delete
        # its own executable while it is open.
        if is_protected_relative_path(relative):
            continue
        if candidate.is_file():
            candidate.unlink()
        elif candidate.is_dir():
            try:
                candidate.rmdir()
            except OSError:
                pass
    for candidate in sorted(resolved_backup.rglob("*")):
        relative = candidate.relative_to(resolved_backup)
        if is_protected_relative_path(relative):
            continue
        destination = resolved_installation / relative
        if candidate.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif candidate.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, destination)


def _service_is_running() -> bool:
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe' or name='pythonw.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return "main.py" in result.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        # A missing legacy WMIC should not make an update unsafe by pretending
        # success.  The batch entry performs the same check before Python starts.
        return False


def sync_dependencies(root: Path) -> None:
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = root / ".venv" / "bin" / "python"
    if not python.is_file():
        raise UpdateError("未找到 .venv；请先完成首次安装后再更新。")
    bundled_uv = root / "tools" / "uv.exe"
    if not bundled_uv.is_file():
        bundled_uv = root / "tools" / "uv"
    if bundled_uv.is_file():
        command = [str(bundled_uv), "pip", "sync", "requirements.lock", "--python", str(python)]
    else:
        command = [str(python), "-m", "pip", "install", "-r", "requirements.lock"]
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        raise UpdateError("依赖同步失败；程序文件已更新但没有回滚，请检查网络或终端错误后重试。")


def choose_release(releases: list[dict[str, Any]], noninteractive: bool) -> dict[str, Any]:
    if not releases:
        raise UpdateError("GitHub 上没有可用 release。")
    print("可选版本（默认最新）：")
    for index, release in enumerate(releases, 1):
        suffix = "（预发布）" if release.get("prerelease") else ""
        print(f"  {index}. {release.get('tag_name', '未命名版本')}{suffix}")
    choice = "" if noninteractive else input("请选择版本编号（直接回车=最新）: ")
    return releases[parse_release_choice(choice, releases)]


def update(root: Path, args: argparse.Namespace) -> None:
    if _service_is_running():
        raise UpdateError("检测到 PresenceKit 服务仍在运行。请先停止服务后再更新。")
    old = current_version(root)
    if args.source_zip:
        archive = Path(args.source_zip).resolve()
        checksum = Path(args.sha256_file).resolve() if args.sha256_file else archive.with_suffix(archive.suffix + ".sha256")
        target = args.target_version or "本地测试包"
        if not archive.is_file() or not checksum.is_file():
            raise UpdateError("本地测试包或其 .sha256 文件不存在。")
        # Local rehearsal inputs can be validated completely before an
        # installation-local staging directory is created.
        validate_v1_update(root, old, target)
        verify_sha256(archive, checksum)
    else:
        release = choose_release(fetch_releases(root), args.yes)
        target = str(release.get("tag_name") or "所选版本")
        validate_v1_update(root, old, target)
        zip_asset, checksum_asset = _release_assets(release)
    stage = root / "_update_tmp"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        if not args.source_zip:
            archive = stage / str(zip_asset["name"])
            checksum = stage / str(checksum_asset["name"])
            opener = _opener(root)
            print(f"当前版本：{old} → 目标版本：{target}")
            download(str(zip_asset["browser_download_url"]), archive, opener)
            download(str(checksum_asset["browser_download_url"]), checksum, opener)
            verify_sha256(archive, checksum)
        source = extract_release(archive, stage / "package")
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    backup = apply_release(root, source, old, reuse_existing_backup=old == target)
    if not args.skip_sync:
        sync_dependencies(root)
    shutil.rmtree(stage)
    print(f"更新完成：{old} → {current_version(root)}")
    print(f"已备份被替换的程序文件：{backup.name}（只保留最近一份）")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="非交互模式：选择最新版本并确认降级")
    parser.add_argument("--source-zip", help="本地 release zip（仅用于离线演练）")
    parser.add_argument("--sha256-file", help="本地 release zip 的 .sha256 文件")
    parser.add_argument("--target-version", help="离线演练显示的目标版本")
    parser.add_argument("--skip-sync", action="store_true", help="跳过依赖同步（仅用于离线演练）")
    parser.add_argument("--restore-backup", help="显式从 updater 创建的升级前备份恢复；这是 downgrade/失败后的人工路线")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.restore_backup:
            if _service_is_running():
                raise UpdateError("检测到 PresenceKit 服务仍在运行。请先停止服务后再恢复备份。")
            restore_installation_from_backup(root, Path(args.restore_backup))
            print(f"已从升级前备份恢复：{args.restore_backup}")
        else:
            update(root, args)
    except UpdateError as exc:
        print(f"[update] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
