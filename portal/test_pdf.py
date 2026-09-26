import io,json
from unittest.mock import Mock,patch
import pymupdf as fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from .tests import PortalTests
from .models import Document
from .pdf_engine import edit_pdf,PDFError,preview_pdf

def fixture():
    doc=fitz.open();page=doc.new_page(width=595,height=842)
    page.insert_text((60,65),'PDF editing test',fontsize=22)
    page.insert_text((60,115),'Amount: USD 100',fontsize=14)
    page.insert_text((60,145),'Reference: ABC-4827',fontsize=12)
    page.draw_rect(fitz.Rect(55,95,330,165),color=(.2,.5,.3))
    page.insert_text((60,205),'Customer name:',fontsize=12)
    w=fitz.Widget();w.field_name='customer_name';w.field_type=fitz.PDF_WIDGET_TYPE_TEXT;w.rect=fitz.Rect(60,220,330,250);w.field_value='';w.text_fontsize=12;page.add_widget(w)
    doc.new_page().insert_text((60,80),'Second page - keep or rotate',fontsize=16)
    doc.new_page().insert_text((60,80),'Third page - delete in test',fontsize=16)
    raw=doc.tobytes();doc.close();return raw

from django.test import TestCase, override_settings

@override_settings(SECURE_SSL_REDIRECT=False)
class PDFTests(TestCase):
    setUp = PortalTests.setUp
    def test_pdf_edits_and_form_values(self):
        original=fixture()
        output,changes,page=edit_pdf(original,{'operations':[{'type':'replace_text','page':1,'find':'USD 100','replace':'USD 250'},{'type':'fill_field','page':1,'field':'customer_name','value':'Alex Morgan'},{'type':'rotate_page','page':2,'degrees':90},{'type':'delete_page','page':3}]})
        with fitz.open(stream=output,filetype='pdf') as doc:
            self.assertEqual(len(doc),2);self.assertEqual(doc[1].rotation,90)
            self.assertIn('USD 250',doc[0].get_text());self.assertNotIn('USD 100',doc[0].get_text());self.assertIn('Reference: ABC-4827',doc[0].get_text())
        from pypdf import PdfReader
        self.assertEqual(PdfReader(io.BytesIO(output)).get_fields()['customer_name']['/V'],'Alex Morgan')
        self.assertEqual(page,1);self.assertEqual(len(changes),4)
        self.assertTrue(preview_pdf(output).startswith(b'\x89PNG'))
        self.assertIn(b'PDF',original[:8])
    def test_pdf_invalid_edits(self):
        for op in [{'type':'delete_page','page':9},{'type':'run_code','page':1},{'type':'replace_text','page':1,'find':'not present','replace':'x'},{'type':'replace_text','page':1,'find':'USD 100','replace':'too much text '*100}]:
            with self.assertRaises(PDFError):edit_pdf(fixture(),{'operations':[op]})
        with self.assertRaises(PDFError):edit_pdf(fixture(),{'operations':[{'type':'delete_page','page':i} for i in (1,2,3)]})
    def test_pdf_api_ownership_and_original_preserved(self):
        raw=fixture();upload=self.client.post(f'/api/chats/{self.chat.id}/upload/',{'file':SimpleUploadedFile('source.pdf',raw)})
        pk=upload.json()['id'];before=Document.objects.get(pk=pk).file.read()
        response=self.client.post(f'/api/pdf/{pk}/edit/',json.dumps({'operations':[{'type':'replace_text','page':1,'find':'USD 100','replace':'USD 250'}]}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content);result=response.json()['id']
        self.assertNotEqual(pk,result);self.assertEqual(Document.objects.get(pk=pk).file.read(),before)
        self.assertEqual(self.client.get(f'/pdf/{pk}/').status_code,200)
        self.client.force_login(self.b)
        for url in [f'/pdf/{pk}/',f'/api/pdf/{result}/preview/',f'/api/pdf/{pk}/info/',f'/api/files/{result}/']:
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(f'/api/pdf/{pk}/edit/',{},content_type='application/json').status_code,404)
    def test_pdf_ai_plan_is_applied_and_requires_csrf(self):
        r=self.client.post(f'/api/chats/{self.chat.id}/upload/',{'file':SimpleUploadedFile('source.pdf',fixture())});pk=r.json()['id']
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.a)
        self.assertEqual(client.post(f'/api/pdf/{pk}/edit/',{},content_type='application/json').status_code,403)
        mocked=Mock();mocked.json.return_value={'choices':[{'message':{'content':json.dumps({'operations':[{'type':'replace_text','page':1,'find':'USD 100','replace':'USD 250'}]})}}]}
        with patch('portal.pdf_views.requests.post',return_value=mocked):
            response=self.client.post(f'/api/pdf/{pk}/edit/',json.dumps({'instruction':'Change USD 100 to USD 250','page':1}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
