from django.test import SimpleTestCase
from .context_budget import build_messages,units,INPUT_BUDGET,IMAGE_RESERVE,clip
class ContextBudgetTests(SimpleTestCase):
    def test_multilingual_input_fits_with_images(self):
        history=[{'role':'assistant','content':'中文😀'*10000+'END'}]
        for count in (0,1,2):
            messages=build_messages('system','报告'*100,['文件一\n'+'财务😀'*5000,'file2\n'+'data'*5000],history,count)
            self.assertLessEqual(sum(units(m['content']) for m in messages)+count*IMAGE_RESERVE,INPUT_BUDGET)
            self.assertEqual(messages[-1]['content'],'报告'*100)
            self.assertTrue(messages[1]['content'].endswith('END'))
    def test_oversized_message_rejected(self):
        with self.assertRaisesMessage(ValueError,'Shorten'):build_messages('system','中'*4000,[],[],0)
    def test_short_history_preserved(self):
        h=[{'role':'user','content':'hello'},{'role':'assistant','content':'hi'}]
        self.assertEqual(build_messages('system','next',[],h)[1:-1],h)
    def test_utf8_clip_bounds(self):
        for n in range(20):
            for tail in (False,True):self.assertLessEqual(units(clip('中文😀abcdef',n,tail)),n)
