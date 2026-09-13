import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from core import character_document_library as library, media_processor as media
from core.tool_dispatcher import _reread_image_wrapper


@pytest.mark.asyncio
async def test_image_modes_scope_and_expired_original(sandbox, monkeypatch):
    from core import image_recognition, llm_client
    out = io.BytesIO()
    Image.new('RGB', (4, 4)).save(out, format='PNG')
    data = out.getvalue()
    digest = media._hash_bytes(data)
    path = sandbox.inbox_dir() / 'fixture.png'
    path.write_bytes(data)
    media._save_image_cache(digest, 'stored description', path, 'fixture.png')
    library.store_upload(uid='owner-test', char_id='char-test', filename='fixture.png',
                         media_type='image/png', sha256=digest, searchable_text='stored description', source='upload_image')
    vision, ocr = AsyncMock(return_value='fresh vision'), AsyncMock(return_value='fresh OCR')
    monkeypatch.setattr(llm_client, 'chat', vision)
    monkeypatch.setattr(image_recognition, 'recognize_ocr', ocr)
    monkeypatch.setattr(image_recognition, 'settings', lambda: {'mode': 'ocr'})
    args = dict(user_id='owner-test', char_id='char-test', sha256=digest)
    assert 'stored description' in await _reread_image_wrapper(**args)
    vision.assert_not_called()
    ocr.assert_not_called()
    assert await _reread_image_wrapper(**args, mode='vision') == 'fresh vision'
    assert await _reread_image_wrapper(**args, mode='ocr') == 'fresh OCR'
    assert '找不到' in await _reread_image_wrapper(**{**args, 'char_id': 'other-char'}, mode='vision')
    assert vision.await_count == 1
    path.unlink()
    assert 'stored description' in await _reread_image_wrapper(**args)
    assert '失败' in await _reread_image_wrapper(**args, mode='vision')


def test_document_summary_long_text_and_keyword_context(sandbox):
    text = 'introduction ' + 'x' * 15000 + '\nneedle detail\n' + 'y' * 3000
    doc = library.store_upload(uid='owner-test', char_id='char-test', filename='long.txt',
                               media_type='text/plain', sha256='a' * 64, searchable_text=text, source='upload_file')
    summary = library.read('owner-test', 'char-test', doc, mode='summary')
    assert summary['content'].startswith('introduction')
    assert summary['next_offset'] is None
    page = library.read('owner-test', 'char-test', doc, query='needle')
    assert 'needle detail' in page['content']
    assert page['offset'] > 12000
    assert page['next_offset'] is not None
    assert library.read('owner-test', 'other-char', doc, query='needle') is None
