import importlib
from html.parser import HTMLParser
from pathlib import Path
import unittest

import app
import common as c

ROOT=Path('/home/funboy/StrixHaloQw38FN')
WB=ROOT/'runtime/gufo/workbench'

class IDs(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[]; self.scripts=[]; self.links=[]
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if data.get('id'): self.ids.append(data['id'])
        if tag=='script' and data.get('src'): self.scripts.append(data['src'])
        if tag=='link' and data.get('href'): self.links.append(data['href'])

class HaloCluUITests(unittest.TestCase):
    def test_shell_is_haloclu_and_ids_are_unique(self):
        text=(WB/'haloclu.html').read_text()
        parser=IDs(); parser.feed(text)
        self.assertEqual(len(parser.ids),len(set(parser.ids)))
        self.assertIn('/haloclu-app.js?v=ui013',parser.scripts)
        self.assertIn('/haloclu.css?v=ui002',parser.links)
        self.assertIn('/haloclu-workbench.css?v=ui007',parser.links)
        source=text
        self.assertIn('id="chat-cap" type="number" min="1" max="32768" value="32768"',source)
        self.assertIn('MiMo-V2.6-Flash-MOPD · NODE02 · 131072',source)
        for required in ('sidebar','tab-chat','tab-coding','tab-writing','tab-models','tab-benchmark','tab-cluster','tab-options','chat-form','chat-recover','coding-recover','writing-recover','coding-files','writing-chapter','project-dialog','project-search','model-selector','chat-copy-conversation','chat-export-conversation','chat-delete-conversation','chat-follow','chat-quality-profile','coding-quality-profile','writing-quality-profile','chat-reasoning-effort','auto-send','show-delivery-attempts','show-job-json'):
            self.assertIn(required,parser.ids)
        self.assertNotIn('<iframe',text.lower())
        self.assertNotIn('Gufo Workbench</title>',text)
        self.assertIn('>HaloClu<',text)

    def test_static_allowlist_is_closed_and_assets_exist(self):
        expected={'/','/haloclu-app.js','/haloclu-reading.mjs','/haloclu-workbench.css','/haloclu.css','/ui-core.mjs','/assets/haloclu-horizontal.png','/assets/haloclu-icon.png','/favicon.ico'}
        self.assertEqual(set(app.STATIC_ASSETS),expected)
        for route,(source,mime) in app.STATIC_ASSETS.items():
            self.assertTrue(source.is_file(),route)
            self.assertTrue(source.resolve().is_relative_to(ROOT.resolve()))
            if source.suffix in ('.png','.ico'):
                self.assertNotIn('charset',mime)
        self.assertNotIn('/web/app.js',app.STATIC_ASSETS)

    def test_multi_backend_controls_are_conversation_scoped(self):
        html=(WB/'haloclu.html').read_text();js=(WB/'haloclu-app.js').read_text()
        for value in ('project-backend','chat-backend','coding-backend','writing-backend','chat-branch','coding-branch','writing-branch','model-selector'):
            self.assertIn(f'id="{value}"',html)
        self.assertIn("sessionStorage.setItem('haloclu.currentProject'",js)
        self.assertIn('project_version:state.project.version',js)
        self.assertIn('client_request_id=crypto.randomUUID()',js)
        self.assertNotIn("localStorage.setItem('haloclu.currentProject'",js)

    def test_browser_client_uses_only_workbench_api(self):
        text=(WB/'haloclu-app.js').read_text()
        self.assertNotIn('127.0.0.1:18561',text)
        self.assertNotIn('/v1/',text)
        self.assertNotIn('X-HaloClu-Session',text)
        self.assertNotIn('iframe',text.lower())
        self.assertIn("'X-Gufo-Request':'1'",text)
        self.assertIn("localStorage.setItem('haloclu.preferences'",text)
        self.assertIn("/recover`,{}",text)
        self.assertIn("job.recovery_eligible===true",text)
        self.assertIn("Primaria terminata per limite output",text)
        self.assertIn("packet.message",text)
        self.assertIn('Last saved event',text.replace('last saved event','Last saved event'))
        self.assertIn('scheduleProjectRender',text)
        self.assertIn("createReadingController",text)
        self.assertIn("reading.captureChat",text)
        self.assertIn("reading.restoreChat",text)
        self.assertIn("requestAnimationFrame",text)
        self.assertNotIn("root.replaceChildren();\n  if(p.brief)",text)
        self.assertNotIn("root.scrollTop=root.scrollHeight",text)
        self.assertIn('Delivery attempts',text)
        self.assertIn('delivered-files.zip',text)
        self.assertIn('selected-delivered-files.zip',text)
        self.assertIn('Selected delivery',text)
        self.assertIn("reading.clearDraft(promptNode.id)",text)
        self.assertIn('renderUserMessage(job)',text)
        self.assertIn('/export/conversation.json',text)
        self.assertIn('/delete`',text)
        for forbidden in ('localStorage.setItem(\'token', 'localStorage.setItem("token', '/v1/downloads', 'workspace-shell'):
            self.assertNotIn(forbidden,text)

    def test_original_brand_assets_are_reused_unchanged(self):
        self.assertEqual(app.STATIC_ASSETS['/assets/haloclu-horizontal.png'][0],ROOT/'web/assets/haloclu-horizontal.png')
        self.assertEqual(app.STATIC_ASSETS['/assets/haloclu-icon.png'][0],ROOT/'web/assets/haloclu-icon.png')
        self.assertEqual(app.STATIC_ASSETS['/haloclu.css'][0],ROOT/'web/styles.css')
        self.assertEqual(app.STATIC_ASSETS['/ui-core.mjs'][0],ROOT/'web/ui-core.mjs')

    def test_conversation_controls_are_visible_and_model_switch_is_compact(self):
        html=(WB/'haloclu.html').read_text();js=(WB/'haloclu-app.js').read_text();css=(WB/'haloclu-workbench.css').read_text()
        for value in ('Copy conversation','Export JSON','Delete','Search conversations','Model','New content · Follow output','MiMo profile','Thinking','Enter sends in Chat','Show delivery attempts','Show technical job.json export'):
            self.assertIn(value,html)
        for value in ('renderUserMessage','editAndResend','copyConversation','exportConversation','deleteProject','changeModel','selectedReasoningEffort','fillMessageMetrics','startLiveMetricTimer','show_delivery_attempts','show_job_json','enter_to_send'):
            self.assertIn(value,js)
        self.assertNotIn("message-avatar','YOU",js)
        self.assertIn("width:95%;max-width:95%",css)
        self.assertIn("justify-content:flex-end",css)
        self.assertIn('last_activity',js)
        self.assertIn('.model-selector',css)
        self.assertIn('.project-row',css)
        self.assertIn('.message-actions',css)

    def test_product_boundaries_are_explicit(self):
        html=(WB/'haloclu.html').read_text()
        for text in ('No GLM/Pi gateway','No benchmark, download or lifecycle controls','Patch application remains manual'):
            self.assertIn(text,html)
        self.assertNotIn('Direct · changes original immediately',html)
        self.assertNotIn('Run command',html)

if __name__=='__main__':
    unittest.main(verbosity=2)
