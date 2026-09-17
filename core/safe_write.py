import gzip
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def safe_write_text(path: Path, content: str, encoding: str = "utf-8") -> bool:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding=encoding)
        with open(tmp, "r+b") as _fd:
            _fd.flush()
            os.fsync(_fd.fileno())
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"[safe_write] 写入失败 {path}: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def safe_write_bytes(path: Path, content: bytes) -> bool:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(content)
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"[safe_write] 写入失败 {path}: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def safe_write_json(path: Path, data: dict | list, *, keep_bak: bool = True) -> bool:
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        # post-write verify: if the tmp file can't be parsed back, abort before replacing
        json.loads(tmp.read_text(encoding="utf-8"))
        with open(tmp, "r+b") as _fd:
            _fd.flush()
            os.fsync(_fd.fileno())
        if keep_bak and path.exists():
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except Exception:
                pass
        tmp.replace(path)
        return True
    except Exception as e:
        logger.error(f"[safe_write] JSON 写入/校验失败，已保留原文件 {path}: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def rotate_jsonl_if_needed(path: Path, max_bytes: int = 5 * 1024 * 1024, keep_n: int = 3) -> bool:
    """若 path 超过 max_bytes，将其 gzip 压缩为 .N.gz 并保留最多 keep_n 份归档。返回是否执行了滚动。"""
    path = Path(path)
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return False
        # 把已有归档向后移一位；超出 keep_n 的直接删
        for k in range(keep_n, 0, -1):
            src = path.with_name(path.name + f".{k}.gz")
            if k == keep_n:
                if src.exists():
                    src.unlink()
            else:
                dst = path.with_name(path.name + f".{k + 1}.gz")
                if src.exists():
                    src.replace(dst)
        # 压缩当前文件为 .1.gz，然后清空
        archive = path.with_name(path.name + ".1.gz")
        with open(path, "rb") as f_in, gzip.open(archive, "wb") as f_out:
            f_out.write(f_in.read())
        path.write_bytes(b"")
        logger.info("[safe_write] jsonl 已滚动: %s → %s", path.name, archive.name)
        return True
    except Exception as e:
        logger.error("[safe_write] jsonl rotation 失败 %s: %s", path, e)
        return False


_DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def archive_old_day_files(dir_path: Path, cutoff_days: int = 30) -> int:
    """将 dir_path 下文件名匹配 YYYY-MM-DD.md 且早于 cutoff_days 天的文件 gzip 压缩归档。
    已有 .gz 的跳过。返回本次归档文件数。
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=cutoff_days)
    count = 0
    for f in dir_path.iterdir():
        if not _DAY_FILE_RE.match(f.name):
            continue
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if file_date >= cutoff:
            continue
        archive = f.with_suffix(f.suffix + ".gz")
        if archive.exists():
            f.unlink()  # .gz 已存在时删掉原文件（之前中断的情况）
            continue
        try:
            with open(f, "rb") as f_in, gzip.open(archive, "wb") as f_out:
                f_out.write(f_in.read())
            f.unlink()
            count += 1
        except Exception as e:
            logger.error("[safe_write] day file archive 失败 %s: %s", f, e)
    if count:
        logger.info("[safe_write] 已归档 %d 个按天文件: %s", count, dir_path)
    return count


def safe_append_jsonl(path: Path, record: dict) -> bool:
    """追加一行 JSON 到 .jsonl 文件（asyncio 单线程安全，进程级原子性）。"""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        return True
    except Exception as e:
        logger.error(f"[safe_write] jsonl 追加失败 {path}: {e}")
        return False
