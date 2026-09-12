"""Portable, explicit installation of the optional native reader (no import I/O)."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = 'v2.5.0'
SOURCE_COMMIT = '6583124dfda92312b6bc19a042a6acfae63fe498'
BROWSER_COMMIT = 'd28e37c7448672550580fca8ff26404aed09a21f'


def install_dir() -> Path:
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        return base / 'PresenceKit' / 'xiaohongshu'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'PresenceKit' / 'xiaohongshu'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'presencekit' / 'xiaohongshu'


def executable() -> Path:
    return install_dir() / ('xiaohongshu-mcp-managed.exe' if sys.platform == 'win32' else 'xiaohongshu-mcp-managed')


def supported() -> bool:
    return sys.platform in {'win32', 'linux', 'darwin'} and platform.machine().lower() in {'amd64', 'x86_64', 'arm64', 'aarch64'}


def _extract(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            path = (target / entry.filename).resolve()
            if not path.is_relative_to(target.resolve()) or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('unsafe_archive')
        source.extractall(target)


def install() -> None:
    """Build pinned upstream source; never overwrite an existing installation/cookie."""
    if not supported():
        raise RuntimeError('unsupported_platform')
    if executable().is_file():
        return
    go = shutil.which('go')
    if not go:
        raise RuntimeError('go_required')
    destination = install_dir()
    destination.mkdir(parents=True, exist_ok=True)
    # Same volume permits an atomic final rename. Partial downloads/builds are disposable.
    with tempfile.TemporaryDirectory(prefix='install-', dir=destination) as scratch:
        root = Path(scratch)
        for repo, commit in [('xiaohongshu-mcp', SOURCE_COMMIT), ('headless_browser', BROWSER_COMMIT)]:
            archive = root / (repo + '.zip')
            with urllib.request.urlopen(f'https://codeload.github.com/xpzouying/{repo}/zip/{commit}', timeout=60) as response:
                with archive.open('wb') as out:
                    shutil.copyfileobj(response, out)
            _extract(archive, root)
        source = root / ('xiaohongshu-mcp-' + SOURCE_COMMIT)
        browser = root / ('headless_browser-' + BROWSER_COMMIT)
        code_path = browser / 'headless_browser.go'
        code = code_path.read_text(encoding='utf-8')
        marker = 'l := launcher.New().'
        if code.count(marker) != 1:
            raise RuntimeError('upstream_changed')
        # Use rod's normal direct launch on Windows; no antivirus exclusions.
        code_path.write_text(code.replace(marker, marker + '\n\t\tLeakless(runtime.GOOS != "windows").'), encoding='utf-8')
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        subprocess.run([go, 'mod', 'edit', '-replace', f'github.com/xpzouying/headless_browser=../{browser.name}'],
                       cwd=source, check=True, timeout=30, **flags)
        output = root / executable().name
        subprocess.run([go, 'build', '-trimpath', '-ldflags', f'-X main.version={VERSION}-presencekit', '-o', str(output), '.'],
                       cwd=source, check=True, timeout=600, **flags)
        output.replace(executable())


if __name__ == '__main__':
    try:
        install()
        print('installed')
    except Exception as exc:
        # CLI/API never echo environment, credentials, compiler output or private paths.
        reason = str(exc) if str(exc) in {'go_required', 'unsupported_platform', 'upstream_changed'} else 'install_failed'
        print(reason, file=sys.stderr)
        raise SystemExit(1)
