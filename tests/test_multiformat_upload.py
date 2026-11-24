"""Real upload parsing, persistence and untrusted-caption boundary regressions."""
import io
import zipfile
from types import SimpleNamespace

import pytest

from core import character_document_library as library, media_processor as media
from tests.test_chat_media import _client, _scoped


@pytest.mark.parametrize('channel', ['mobile', 'desktop'])
def test_csv_upload_reaches_turn_and_archives_full_text(sandbox, monkeypatch, channel):
    from admin.routers import chat as router
    captured = []

    async def turn(text, channel, **kwargs):
        captured.append((text, kwargs))
        return {'reply': 'ok', 'turn_id': 'csv-turn', 'msg_id': 'csv-turn'}

    monkeypatch.setattr('core.owner_chat.service.run_owner_chat_turn', turn)
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'scheduler': {'owner_id': 'reader'}})
    monkeypatch.setattr('core.pipeline_registry.get', lambda: SimpleNamespace(_active_character_id='char-test'))
    client = _client(monkeypatch, owner="reader", char="char-test")
    _scoped(sandbox, 'emt_chat', ['chat'])
    response = client.post('/upload/ingest', headers={'Authorization': 'Bearer emt_chat'},
                           files=[('files', ('ledger.CSV', '项目,金额\n午餐,25\n'.encode('gb18030'), 'text/csv'))],
                           data={'channel': channel, 'message': '看看这份表'})
    assert response.status_code == 200, response.text
    assert '午餐' in captured[0][0] and '25' in captured[0][0]
    assert captured[0][1]['trusted_user_text'] == '看看这份表'
    doc = library.search('reader', 'char-test')[0]['document_id']
    assert '午餐' in library.read('reader', 'char-test', doc)['content']
    assert response.json()['media_refs'][0]['filename'] == 'ledger.CSV'


def archive(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return out.getvalue()


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'gb18030'])
def test_csv_retains_empty_cells_quotes_and_multiline_values(encoding):
    text = '名称,金额,备注\n午餐,25,"两行\n说明"\n空项,,"a,b"\n'
    parsed = media.parse_file_bytes(text.encode(encoding), 'table.csv')
    assert '午餐' in parsed and '两行' in parsed and '说明' in parsed
    assert 'a,b' in parsed and '空项' in parsed
    import json
    rows = [json.loads(line.split(': ', 1)[1]) for line in parsed.splitlines()]
    assert rows[1] == ['午餐', '25', '两行\n说明']
    assert rows[2] == ['空项', '', 'a,b']


def test_xlsx_keeps_sheet_cell_positions_and_formula_without_evaluation():
    data = archive({
        'xl/workbook.xml': '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="预算" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/sharedStrings.xml': '<sst><si><t>午餐</t></si></sst>',
        'xl/worksheets/sheet1.xml': '<worksheet><sheetData><row r="3"><c r="A3" t="s"><v>0</v></c><c r="C3"><v>25</v></c><c r="D3"><f>SUM(C3)</f><v>25</v></c></row></sheetData></worksheet>',
    })
    parsed = media.parse_file_bytes(data, 'budget.xlsx')
    assert all(value in parsed for value in ['预算', 'A3', '午餐', 'C3', '25', '=SUM(C3)'])


def test_pptx_preserves_presentation_order_and_tables():
    data = archive({
        'ppt/presentation.xml': '<presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sldIdLst><sldId r:id="rId2"/><sldId r:id="rId1"/></sldIdLst></presentation>',
        'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>',
        'ppt/slides/slide1.xml': '<slide><p><t>结尾</t></p></slide>',
        'ppt/slides/slide2.xml': '<slide><p><t>开头</t></p><tbl><tr><tc><p><t>金额</t></p></tc><tc><p><t>25</t></p></tc></tr></tbl></slide>',
    })
    parsed = media.parse_file_bytes(data, 'slides.pptx')
    assert parsed.index('开头') < parsed.index('结尾')
    assert '金额' in parsed and '25' in parsed


def test_html_excludes_scripts_and_styles():
    parsed = media.parse_file_bytes(b'<h1>Title</h1><script>secret()</script><style>.hidden{}</style><p>Body &amp; text</p>', 'page.html')
    assert 'Title' in parsed and 'Body & text' in parsed
    assert 'secret' not in parsed and '.hidden' not in parsed


@pytest.mark.parametrize('filename,data', [('broken.xlsx', b'broken'), ('broken.pdf', b'%PDF-bad'), ('bad.csv', b'\x00\x01')])
def test_malformed_documents_raise_typed_failure(filename, data):
    with pytest.raises(media.MediaIngestError):
        media.parse_file_bytes(data, filename)


def test_archive_expansion_rejected_before_parsing(monkeypatch):
    from core import document_parsers
    monkeypatch.setattr(document_parsers, 'MAX_EXPANDED_BYTES', 100)
    with pytest.raises(media.MediaIngestError) as error:
        media.parse_file_bytes(archive({'xl/workbook.xml': 'x' * 101}), 'bomb.xlsx')
    assert error.value.code == 'document_container_too_large'


def pdf_bytes(*, text=False, password=None):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 100 Td (PDF evidence) Tj ET')
        page[NameObject('/Contents')] = stream
        writer.add_blank_page(width=200, height=200)
    if password:
        writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_text_and_missing_page_coverage_are_explicit():
    parsed = media.parse_file_bytes(pdf_bytes(text=True), 'evidence.pdf')
    assert 'PDF evidence' in parsed and '第 1 页' in parsed
    assert '第 2 页' in parsed and '无可提取文字' in parsed


@pytest.mark.parametrize('password,code', [(None, 'document_needs_ocr'), ('test-only-password', 'file_parse_failed')])
def test_pdf_without_readable_text_or_encrypted_fails_loudly(password, code):
    with pytest.raises(media.MediaIngestError) as exc:
        media.parse_file_bytes(pdf_bytes(password=password), 'scan.pdf')
    assert exc.value.code == code
