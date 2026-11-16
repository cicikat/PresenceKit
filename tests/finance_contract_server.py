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
    marker = root / '.finance-contract-fixture'
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        parser.error('Refusing to overwrite a nonempty directory without a fixture marker')
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text('Disposable finance contract fixture\n', encoding='utf-8', newline='\n')
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
    # Public, disposable mobile-profile credential: the live device path needs no admin scope.
    from admin.token_registry import hash_token
    token_path = sandbox.get_paths().auth_tokens_file()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(yaml.safe_dump({'tokens': [{
        'label': 'finance-fixture-mobile', 'hash': hash_token('emt_fixture_finance_mobile_330'),
        'scopes': ['profile:mobile'], 'disabled': False,
    }]}), encoding='utf-8', newline='\n')
    from admin.admin_server import app
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18730)


if __name__ == '__main__':
    main()
