"""Disposable loopback server for the opt-in Android finance contract test.

Run from the backend root with --state-dir .tmp/finance-contract.
Only public fixture credentials and a test DataPaths root are used.
"""
import argparse
import os
from pathlib import Path
import sys

import yaml


def main():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    root = Path(args.state_dir).resolve()
    if root == project or root == project / 'data' or root == project / 'userdata':
        parser.error('Use a dedicated disposable fixture directory')
    root.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((project / 'config.example.yaml').read_text(encoding='utf-8'))
    config['scheduler']['owner_id'] = 'fixture-owner'
    config['admin']['secret_key'] = 'fixture-preview-only-330'
    config['finance_ingest'] = {'enabled': True, 'character_readable': False}
    path = root / 'config.yaml'
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8', newline='\n')
    os.environ['PRESENCEKIT_CONFIG_PATH'] = str(path)
    import core.sandbox as sandbox
    from core.data_paths import DataPaths
    sandbox._instance = DataPaths(mode='test', test_session_id='finance330-contract', project_root=root)
    os.environ['YEXUAN_DATA_PREFIX'] = str(sandbox.get_paths()._base.resolve())
    from admin.admin_server import app
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18730)


if __name__ == '__main__':
    main()
