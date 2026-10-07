"""Create a local, unencrypted ZIP from a verified offline private snapshot."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import backup_state as backup


def create_zip(installation: Path) -> Path:
    installation = installation.resolve()
    name = f"presencekit-data-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.zip"
    output = installation / name
    partial = output.with_suffix(".zip.partial")
    try:
        # The existing snapshot API deliberately requires staging outside live state.
        with tempfile.TemporaryDirectory(prefix="presencekit-weekly-", dir=installation.parent) as temp:
            snapshot = Path(temp) / "snapshot"
            print("正在检查服务状态并创建离线快照，请稍候……", flush=True)
            backup.create_snapshot(installation, snapshot, protection_mode="protected_volume")
            print("离线快照已完成，正在压缩 ZIP……", flush=True)
            manifest = json.loads((snapshot / backup.MANIFEST_NAME).read_text(encoding="utf-8"))
            with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for file in backup._walk_regular_files(snapshot):
                    archive.write(file, file.relative_to(snapshot).as_posix())
            print("压缩完成，正在逐文件校验 ZIP……", flush=True)
            with zipfile.ZipFile(partial) as archive:
                expected = {r["path"] for r in manifest["files"]} | {backup.MANIFEST_NAME, backup.MANIFEST_CHECKSUM_NAME}
                if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                    raise backup.BackupError("zip_verify_failed", "ZIP 文件清单校验失败。")
                for record in manifest["files"]:
                    digest = hashlib.sha256()
                    with archive.open(record["path"]) as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if archive.getinfo(record["path"]).file_size != record["size"] or digest.hexdigest() != record["sha256"]:
                        raise backup.BackupError("zip_verify_failed", "ZIP 文件内容校验失败。")
                manifest_bytes = archive.read(backup.MANIFEST_NAME)
                if hashlib.sha256(manifest_bytes).hexdigest() != archive.read(backup.MANIFEST_CHECKSUM_NAME).decode("ascii").strip():
                    raise backup.BackupError("zip_verify_failed", "ZIP manifest 校验失败。")
            os.replace(partial, output)
        return output
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    print("开始备份。ZIP 未加密，包含私有数据及凭据，请妥善保存。", flush=True)
    try:
        result = create_zip(Path(__file__).resolve().parents[1])
    except backup.BackupError as exc:
        print(f"备份失败 [{exc.code}]：{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"备份失败：{type(exc).__name__}。请检查磁盘空间及文件权限。", file=sys.stderr)
        sys.exit(1)
    print(f"备份完成并通过逐文件 SHA-256 校验：{result.name}")
