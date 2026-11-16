"""Bounded, offline document extraction. No macros, formulas or links are run."""
import csv
import io
import json
import posixpath
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml',
                 '.xml', '.log', '.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.sql',
                 '.sh', '.ps1', '.ini', '.toml', '.rst'}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {'.docx', '.pdf', '.xlsx', '.pptx', '.html', '.htm'}
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 4096


def bounded_join(parts, separator='\n'):
    from core.document_reading import TEXT_CHARS
    result, size = [], 0
    for part in parts:
        size += len(part) + (len(separator) if result else 0)
        if size > TEXT_CHARS:
            raise error('document_text_too_large', f'解析正文超过 {TEXT_CHARS} 字符上限，请拆分后上传；没有静默截断正文')
        result.append(part)
    return separator.join(result)


def error(code, message):
    from core.media_processor import MediaIngestError
    return MediaIngestError(code, message)


def decode_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ['utf-16']
    else:
        encodings = ['utf-8-sig', 'gb18030']
    for encoding in encodings:
        try:
            value = data.decode(encoding)
            if any(ord(c) < 32 and c not in '\n\r\t\f' for c in value):
                raise error('file_parse_failed', '文件包含二进制内容，请转换为文本或受支持的文档格式')
            return value
        except UnicodeDecodeError:
            continue
    raise error('file_parse_failed', '文本编码无法解析，请转换为 UTF-8')


def checked_archive(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    members = z.infolist()
    if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_EXPANDED_BYTES:
        z.close()
        raise error('document_container_too_large', '文档展开后过大，请拆分后上传')
    if any(i.flag_bits & 1 for i in members):
        z.close()
        raise error('file_parse_failed', '请先解除文档加密再上传')
    return z


def xml(z, name):
    value = z.read(name)
    if b'<!DOCTYPE' in value.upper() or b'<!ENTITY' in value.upper() or b'\x00' in value:
        raise error('file_parse_failed', '文档 XML 格式不受支持，请重新另存后上传')
    return ET.fromstring(value)


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def nodes(root, name):
    return (node for node in root.iter() if tag(node) == name)


def rich_text(root):
    return ''.join(n.text or '' for n in nodes(root, 't'))


def relationships(z, path):
    parent, filename = posixpath.split(path)
    root = xml(z, f'{parent}/_rels/{filename}.rels')
    result = {}
    for rel in root:
        if rel.get('TargetMode') == 'External':
            continue
        target = rel.get('Target', '')
        resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(parent, target))
        if resolved.startswith(parent + '/') and resolved in z.namelist():
            result[rel.get('Id')] = resolved
    return result


def relationship_id(node):
    return node.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')


def extract_xlsx(z):
    from core.document_reading import TEXT_CHARS
    shared = [rich_text(n) for n in nodes(xml(z, 'xl/sharedStrings.xml'), 'si')] if 'xl/sharedStrings.xml' in z.namelist() else []
    rels = relationships(z, 'xl/workbook.xml')
    out = ['[Excel：单元格保留原始值与公式，不执行公式，不解析图表或内嵌图片]']
    size = len(out[0])
    for sheet in nodes(xml(z, 'xl/workbook.xml'), 'sheet'):
        out.append(f'工作表：{sheet.get("name", "")}')
        target = rels.get(relationship_id(sheet))
        if not target:
            raise error('file_parse_failed', '工作表引用无法读取，请重新另存为 XLSX')
        for row in nodes(xml(z, target), 'row'):
            cells = []
            for cell in row:
                if tag(cell) != 'c':
                    continue
                formula = next(nodes(cell, 'f'), None)
                value = next(nodes(cell, 'v'), None)
                raw = value.text or '' if value is not None else ''
                if formula is not None:
                    raw = '=' + (formula.text or '')
                elif cell.get('t') == 's':
                    raw = shared[int(raw)]
                elif cell.get('t') == 'inlineStr':
                    raw = rich_text(cell)
                rendered = f'{cell.get("r", "?")}={json.dumps(raw, ensure_ascii=False)}'
                size += len(rendered) + 3
                if size > TEXT_CHARS:
                    raise error('document_text_too_large', '表格解析正文过大，请拆分后上传；没有静默截断单元格')
                cells.append(rendered)
            out.append(' | '.join(cells))
    return bounded_join(out)


def extract_pptx(z):
    rels = relationships(z, 'ppt/presentation.xml')
    out = ['[演示文稿：提取正文与表格，不解析内嵌图片、图表或动画]']
    for index, slide in enumerate(nodes(xml(z, 'ppt/presentation.xml'), 'sldId'), 1):
        target = rels.get(relationship_id(slide))
        if not target:
            raise error('file_parse_failed', '幻灯片引用无法读取，请重新另存为 PPTX')
        out.append(f'第 {index} 页')
        out.extend(rich_text(p) for p in nodes(xml(z, target), 'p'))
    return bounded_join(out)


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.out = []

    def handle_starttag(self, name, attrs):
        if name in {'script', 'style', 'template'}:
            self.hidden.append(name)
        if not self.hidden and name in {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.out.append('\n')
        if not self.hidden and name in {'td', 'th'}:
            self.out.append('\t')

    def handle_endtag(self, name):
        if self.hidden and self.hidden[-1] == name:
            self.hidden.pop()
        if not self.hidden and name in {'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.out.append('\n')

    def handle_data(self, value):
        if not self.hidden:
            self.out.append(value)


def parse_document(data, filename):
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            text = decode_text(data)
            if suffix in {'.csv', '.tsv'}:
                # JSON arrays preserve empty fields, embedded delimiters and newlines.
                rows = csv.reader(io.StringIO(text, newline=''), delimiter='\t' if suffix == '.tsv' else ',', strict=True)
                return bounded_join(f'行 {i}: {json.dumps(row, ensure_ascii=False)}' for i, row in enumerate(rows, 1))
            return text
        if suffix in {'.html', '.htm'}:
            parser = VisibleHTML()
            parser.feed(decode_text(data))
            return ''.join(parser.out)
        if suffix in {'.xlsx', '.pptx'}:
            with checked_archive(data) as z:
                return extract_xlsx(z) if suffix == '.xlsx' else extract_pptx(z)
        if suffix == '.pdf':
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                from core.media_processor import _missing_dep_error
                raise _missing_dep_error('pypdf', 'PDF 文档') from exc
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise error('file_parse_failed', '请先解除 PDF 加密再上传')
            if len(reader.pages) > 300:
                raise error('document_container_too_large', 'PDF 超过300页，请拆分后上传')
            parts = ['[PDF：仅提取文字；版面、图片和扫描页未进行视觉识别]']
            has_text = False
            for i, page in enumerate(reader.pages, 1):
                # Compressed streams can be much larger than the upload itself.
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > MAX_EXPANDED_BYTES:
                    raise error('document_container_too_large', 'PDF 页面内容过大，请拆分后上传')
                text = page.extract_text() or ''
                has_text |= bool(text.strip())
                parts.append(f'第 {i} 页\n' + (text if text.strip() else '[此页无可提取文字，请将页面作为图片发送以识别]'))
            if not has_text:
                raise error('document_needs_ocr', '这份 PDF 没有可读取文字，请将扫描页作为图片发送，或先转成可搜索 PDF')
            return bounded_join(parts)
        return None
    except Exception as exc:
        from core.media_processor import MediaIngestError
        if isinstance(exc, MediaIngestError):
            raise
        raise error('file_parse_failed', '文件解析失败，请确认格式正确且未加密，或另存为文本后上传') from exc
