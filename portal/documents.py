import io, zipfile
from pathlib import Path
LIMIT = 100000
SUPPORTED = {'.txt','.md','.csv','.json','.pdf','.docx','.xlsx'}
def extract(name, raw):
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED: raise ValueError('Supported documents: PDF, DOCX, XLSX, CSV, TXT, MD, and JSON.')
    if ext in {'.docx','.xlsx'}:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=z.infolist()
            if len(infos)>3000 or sum(i.file_size for i in infos)>80*1024*1024:
                raise ValueError('This file is too large when extracted. Split it before uploading.')
    if ext=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw))
        if reader.is_encrypted: raise ValueError('Remove the PDF password before uploading.')
        if len(reader.pages)>300: raise ValueError('PDFs can contain up to 300 pages. Split this file before uploading.')
        chunks=[]; length=0
        for page in reader.pages:
            text=page.extract_text() or ''
            chunks.append(text); length+=len(text)
            if length>LIMIT: break
        text='\n'.join(chunks)
    elif ext=='.docx':
        from docx import Document
        doc=Document(io.BytesIO(raw))
        text='\n'.join([p.text for p in doc.paragraphs]+[' | '.join(c.text for c in r.cells) for t in doc.tables for r in t.rows])
    elif ext=='.xlsx':
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        chunks=[]; length=0
        for sheet in book:
            chunks.append('Worksheet: '+sheet.title)
            for row in sheet.iter_rows(max_row=10000,max_col=100,values_only=True):
                line=' | '.join(str(v) if v is not None else '' for v in row)
                chunks.append(line); length+=len(line)
                if length>LIMIT: break
            if length>LIMIT: break
        book.close(); text='\n'.join(chunks)
    else:
        try: text=raw.decode('utf-8-sig')
        except UnicodeDecodeError: text=raw.decode('gb18030')
    text=text.replace('\x00','').strip()
    if not text and ext=='.pdf': return '[This PDF contains no extractable text. Use the PDF editor for page operations; text replacement requires a searchable PDF.]',False
    if not text and ext in {'.docx','.xlsx'}: return '[This file contains no extractable text. Use the document tools to view or edit supported contents.]',False
    if not text: raise ValueError('No readable text was found. Run OCR on scanned PDFs before uploading.')
    return text[:LIMIT],len(text)>LIMIT
