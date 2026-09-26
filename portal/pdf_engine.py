"""Bounded PDF operations. All operations write a new document, never the source."""
import io, json
import pymupdf as fitz
from pypdf import PdfReader

class PDFError(ValueError): pass

def open_pdf(raw):
    try: doc=fitz.open(stream=raw,filetype='pdf')
    except Exception as exc: raise PDFError('This file is not a readable PDF.') from exc
    if doc.needs_pass: doc.close(); raise PDFError('Remove the PDF password before editing.')
    if not 1<=len(doc)<=300: doc.close(); raise PDFError('PDFs must contain 1–300 pages.')
    return doc

def inspect_pdf(raw,page=1):
    with open_pdf(raw) as doc:
        if type(page)!=int or not 1<=page<=len(doc): raise PDFError('Invalid page number.')
        p=doc[page-1]
        fields=[{'name':w.field_name,'value':str(w.field_value or ''),'type':w.field_type_string} for w in p.widgets() or []]
        return {'pages':len(doc),'page':page,'text':p.get_text()[:14000],'fields':fields[:100]}

def preview_pdf(raw,page=1):
    with open_pdf(raw) as doc:
        if type(page)!=int or not 1<=page<=len(doc): raise PDFError('Invalid page number.')
        p=doc[page-1]; scale=min(1.5,1400/max(p.rect.width,p.rect.height))
        return p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('png')

def operations(plan,count):
    if not isinstance(plan,dict) or set(plan)-{'operations','error'}: raise PDFError('Invalid edit plan. Please use the exact-edit controls.')
    if plan.get('error'): raise PDFError(str(plan['error'])[:500])
    ops=plan.get('operations')
    if not isinstance(ops,list) or not 1<=len(ops)<=12: raise PDFError('Specify between 1 and 12 edits.')
    schemas={'replace_text':{'type','page','find','replace','all'},'fill_field':{'type','page','field','value'},'rotate_page':{'type','page','degrees'},'delete_page':{'type','page'}}
    deleted=[]
    for op in ops:
        if not isinstance(op,dict) or op.get('type') not in schemas or set(op)-schemas[op['type']]: raise PDFError('Unsupported PDF operation.')
        if type(op.get('page'))!=int or not 1<=op['page']<=count: raise PDFError('An edit refers to an invalid page.')
        kind=op['type']
        if kind=='replace_text':
            if not isinstance(op.get('find'),str) or not 1<=len(op['find'])<=500 or not isinstance(op.get('replace'),str) or len(op['replace'])>1000: raise PDFError('Provide exact text to find and replacement text.')
            if '\n' in op['find'] or '\n' in op['replace']: raise PDFError('Replace one line at a time.')
            if 'all' in op and type(op['all'])!=bool: raise PDFError('Invalid match option.')
        if kind=='fill_field':
            if not isinstance(op.get('field'),str) or not isinstance(op.get('value'),str) or len(op['value'])>1000: raise PDFError('Provide a valid field name and text value.')
        if kind=='rotate_page' and (type(op.get('degrees'))!=int or op['degrees'] not in (90,180,270)): raise PDFError('Rotation must be 90, 180, or 270 degrees clockwise.')
        if kind=='delete_page': deleted.append(op['page'])
    if len(set(deleted))!=len(deleted) or len(deleted)>=count: raise PDFError('Keep at least one page and do not delete a page twice.')
    if any(o['page'] in deleted and o['type']!='delete_page' for o in ops): raise PDFError('Do not edit and delete the same page.')
    return ops

def replace_text(page,op):
    if any(a.type[0]==fitz.PDF_ANNOT_REDACT for a in page.annots() or []): raise PDFError('This page has pending redactions. Resolve them before editing.')
    hits=page.search_for(op['find'])
    hits=[r for r in hits if ' '.join(page.get_textbox(r).split())==' '.join(op['find'].split())]
    if not hits: raise PDFError(f"Exact text was not found on page {op['page']}. Scanned text cannot be replaced directly.")
    if len(hits)>1 and not op.get('all',False): raise PDFError('The text occurs more than once. Enable Replace all matches or use a more specific phrase.')
    if len(hits)>50: raise PDFError('Too many matches. Use a more specific phrase.')
    replacements=[]
    for rect in hits:
        spans=[s for b in page.get_text('dict')['blocks'] if 'lines' in b for line in b['lines'] for s in line['spans'] if fitz.Rect(s['bbox']).intersects(rect)]
        if not spans: raise PDFError('Unable to determine the text position.')
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines',[]):
                if any(fitz.Rect(t['bbox']).intersects(rect) for t in line['spans']) and tuple(line['dir'])!=(1.0,0.0):
                    raise PDFError('Rotated or vertical text cannot be replaced here.')
        span=spans[0]; size=float(span['size'])
        font='china-s' if any(ord(c)>255 for c in op['replace']) else 'helv'
        color=fitz.sRGB_to_pdf(span['color']); baseline=span['origin'][1]
        chosen=size
        if op['replace']:
            width=fitz.get_text_length(op['replace'],fontname=font,fontsize=size)
            if width>rect.width: chosen=size*rect.width/width
            if chosen<6: raise PDFError('Replacement text does not fit the original area. Use shorter text.')
        replacements.append((rect,chosen,font,color,baseline))
    for rect,_,_,_,_ in replacements: page.add_redact_annot(rect,fill=False,cross_out=False)
    page.apply_redactions(images=0,graphics=0)
    for rect,size,font,color,baseline in replacements:
        if op['replace']: page.insert_text((rect.x0,baseline),op['replace'],fontname=font,fontsize=size,color=color)
    return f"Page {op['page']}: replaced {len(hits)} text match(es)."

def edit_pdf(raw,plan):
    with open_pdf(raw) as doc:
        ops=operations(plan,len(doc)); summaries=[]; fields={}
        if doc.get_sigflags()>0: raise PDFError('Digitally signed PDFs cannot be edited here. Use an unsigned copy.')
        for op in ops:
            page=doc[op['page']-1]; kind=op['type']
            if kind=='replace_text': summaries.append(replace_text(page,op))
            elif kind=='rotate_page':
                page.set_rotation((page.rotation+op['degrees'])%360);summaries.append(f"Page {op['page']}: rotated {op['degrees']} degrees clockwise.")
            elif kind=='fill_field':
                widgets=[w for w in page.widgets() or [] if w.field_name==op['field']]
                if len(widgets)!=1: raise PDFError('Form field missing or ambiguous on the selected page.')
                w=widgets[0]
                if w.field_type!=fitz.PDF_WIDGET_TYPE_TEXT: raise PDFError('Only text form fields are supported. Signature and checkbox fields are not changed.')
                if w.field_flags & 1: raise PDFError('This form field is read-only.')
                if op['field'] in fields: raise PDFError('Fill each field once per edit.')
                if any(ord(c)>255 for c in op['value']): raise PDFError('This version supports Latin text in PDF form fields. Non-Latin form values need a compatible embedded font.')
                if w.text_maxlen and len(op['value'])>w.text_maxlen: raise PDFError('The value exceeds the form field character limit.')
                if op['value'] and not (w.field_flags & 4096):
                    needed=fitz.get_text_length(op['value'],fontname='helv',fontsize=1)
                    w.text_fontsize=min(w.text_fontsize or 12,(w.rect.width-4)/max(needed,1),(w.rect.height-4)/1.4)
                    if w.text_fontsize<6: raise PDFError('The form value is too long to fit. Use a shorter value.')
                w.field_value=op['value']; w.update();fields[op['field']]=op['value']
                summaries.append(f"Page {op['page']}: filled field {op['field']}.")
        for op in sorted([o for o in ops if o['type']=='delete_page'],key=lambda o:o['page'],reverse=True):
            doc.delete_page(op['page']-1);summaries.append(f"Page {op['page']}: deleted.")
        output=doc.tobytes(garbage=3,deflate=True)
    if len(output)>20*1024*1024: raise PDFError('The edited PDF exceeds 20 MB. Please use a smaller source.')
    with open_pdf(output) as check:
        if fields:
            canonical=PdfReader(io.BytesIO(output)).get_fields() or {}
            actual={}
            for page in check:
                for w in page.widgets() or []:
                    if w.field_name in fields:
                        if str(w.field_value or '')!=fields[w.field_name]: raise PDFError('Form verification failed. No output was saved.')
                        actual[w.field_name]=w.field_value
            for name,value in fields.items():
                if name not in actual or str(canonical.get(name,{}).get('/V',''))!=value: raise PDFError('Form verification failed. No output was saved.')
    deleted={o['page'] for o in ops if o['type']=='delete_page'}
    target=next((o['page'] for o in ops if o['type']!='delete_page'),1)
    preview=max(1,target-sum(p<target for p in deleted))
    return output,summaries,preview
