"""Dream authored world file operations on already-resolved canonical paths."""

from pathlib import Path
import shutil as _shutil


def create_world_files(template: Path, dest: Path, body: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("ruleset.md", "mes_example.md", "vocab.json", "lorebook.yaml"):
        src = template / name
        if src.exists():
            _shutil.copy2(src, dest / name)

    label = (body.get("label") or "").strip()
    if label:
        import json as _json
        (dest / "meta.json").write_text(
            _json.dumps({"label": label}, ensure_ascii=False, indent=2), encoding="utf-8"
        )



def write_lorebook(directory: Path, entries: list) -> None:
    import yaml as _yaml
    p = directory / "lorebook.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _yaml.dump(entries, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )



def materialize_world(read_dir: Path, write_dir: Path) -> None:
    _shutil.copytree(read_dir, write_dir)


def rename_world(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def delete_world(directory: Path) -> None:
    _shutil.rmtree(directory)


def write_preset(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def delete_preset(path: Path) -> None:
    path.unlink()
