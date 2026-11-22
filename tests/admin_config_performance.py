"""Measure an actual settings POST with isolated synthetic configuration.

Includes auth, three-way merge, atomic persistence, verification and hot reload.
Run directly; reports timings without making CI depend on machine speed.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from admin.auth import TokenInfo
from admin.routers import settings_prompt_language
from core import config_loader


def measure():
    config = {'fixture': [
        {'name': f'fixture-{i}', 'enabled': True, 'params': {'temperature': 0.7, 'max_tokens': 2048}}
        for i in range(400)
    ]}
    app = FastAPI()
    app.include_router(settings_prompt_language.router)
    token = TokenInfo('fixture', frozenset({'admin'}), 'fixture')
    with TemporaryDirectory() as folder:
        path = Path(folder) / 'config.yaml'
        path.write_text(yaml.safe_dump(config), encoding='utf-8', newline='\n')
        with patch.object(config_loader, '_CONFIG_PATH', path), \
             patch.object(config_loader, '_config', None), \
             patch.object(config_loader, '_base_config', None), \
             patch.object(config_loader, '_config_mtime', None), \
             patch.object(config_loader, '_base_config_mtime', None), \
             patch('admin.auth.resolve_token', return_value=token), TestClient(app) as client:
            timings = []
            for language in ['en', 'zh'] * 3:
                start = perf_counter()
                response = client.post('/settings/prompt-language', json={'language': language},
                                       headers={'Authorization': 'Bearer fixture'})
                timings.append(round((perf_counter() - start) * 1000, 2))
                assert response.status_code == 200, response.text
                assert response.json()['effective_language'] == language
                saved = yaml.safe_load(path.read_text(encoding='utf-8'))
                assert saved['fixture'] == config['fixture']
                assert saved['prompt_language'] == language
    return timings


if __name__ == '__main__':
    current = measure()
    # Compare the previous Python safe codecs on exactly the same API and data.
    with patch('core.config_yaml.load_config_yaml', yaml.safe_load), \
         patch('admin.config_control.dump_config_yaml', yaml.safe_dump):
        previous = measure()
    print(json.dumps({'settings_post_ms': current, 'previous_codecs_ms': previous}))
