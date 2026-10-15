"""Persisted framework prompt language shared by every model execution chain."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core import config_loader
from core.prompt_language import language

router = APIRouter()


class PromptLanguageUpdate(BaseModel):
    language: Literal['zh', 'en']


@router.get('/settings/prompt-language')
async def get_prompt_language(auth=Depends(require_scopes('persona'))):
    return {'language': language(), 'effective_language': language(), 'default': 'zh'}


@router.post('/settings/prompt-language')
async def set_prompt_language(body: PromptLanguageUpdate, auth=Depends(require_scopes('admin'))):
    path = config_loader.get_config_path()
    cfg = read_config_file(path)
    cfg['prompt_language'] = body.language
    write_config_file(path, cfg)
    config_loader.reload_config()
    return {'language': language(), 'effective_language': language(), 'default': 'zh'}
