import asyncio
import hashlib
import httpx
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.document_text import class_excerpt
from backend.retrieval import relevant_evidence
from backend.sources import paperless_document_text


def test_driving_license_rejects_unrelated_evidence_and_minimizes_identity():
    question='Seit wann habe ich meinen Motorradführerschein und meinen Autoführerschein?'
    for title in ('Cardio Strong Montageanleitung','Newsletter zur Führerscheinreform','Indeed neue Jobs','Allianz Versicherung Führerschein'):
        assert not relevant_evidence(question,title,'Klasse A und B Führerschein 01.01.2000')
    assert relevant_evidence(question,'Führerschein Michael','')
    text='Geburtstag 01.01.1970\nFührerscheinnummer ABC123\n9 10 11 12\nA 02.03.2001 - - SECRET999\nB 04.05.1999 - -'
    excerpt=class_excerpt(text)
    assert 'A 02.03.2001' in excerpt and 'B 04.05.1999' in excerpt
    assert 'SECRET999' not in excerpt
    assert 'ABC123' not in excerpt and 'Geburtstag' not in excerpt
    assert class_excerpt('4a 01.01.2020\nDokumentdatum 01.01.2025')==''


def test_selected_original_is_downloaded_only_locally_and_ocr_is_cached(tmp_path,monkeypatch):
    from backend.store import Store
    store=Store(tmp_path);s=Settings(_env_file=None,data_dir=tmp_path,paperless_url='https://paperless.example',paperless_token='test')
    instance=hashlib.sha256(b'https://paperless.example/').hexdigest()[:12];identifier='paperless:'+instance+':42'
    calls=[];ocr_calls=[]
    def handle(request):
        assert request.headers['Authorization']=='Token test'
        calls.append(request.url.path)
        if request.url.path.endswith('/download/'):return httpx.Response(200,content=b'fake-image')
        return httpx.Response(200,json={'id':42,'title':'Führerschein','content':'','modified':'2026-01-01','original_file_name':'card.png'})
    original=httpx.AsyncClient
    monkeypatch.setattr('backend.sources.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    monkeypatch.setattr('backend.document_text.ocr_original',lambda data:ocr_calls.append(data) or 'A 02.03.2001\nB 04.05.1999')
    async def scenario():
        for _ in range(2):
            text,ocr=await paperless_document_text(s,identifier,store.document_text_cache,store.save_document_text_cache)
            assert ocr and '02.03.2001' in text
    asyncio.run(scenario())
    assert calls.count('/api/documents/42/download/')==1 and len(ocr_calls)==1


def test_license_chat_uses_only_readable_class_rows_and_no_other_connections(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True,paperless_url='https://paperless.example'))
    app.state.store.commit_item({'id':'paperless:test:42','source':'paperless','version':'1','title':'Führerschein Michael','body':''})
    app.state.store.commit_item({'id':'manual','source':'paperless','version':'1','title':'Laufband Anleitung','body':'Klassen A B 2000 Führerschein Motor'})
    async def forbidden(*args,**kwargs):raise AssertionError('Unrelated lookup')
    async def read(*args,**kwargs):return 'Führerscheinnummer SECRET123\n9 10 11 12\nA 02.03.2001\nB 04.05.1999',True
    async def answer(settings,task,payload):
        assert len(payload['sources'])==1
        assert 'SECRET123' not in str(payload)
        assert 'A 02.03.2001' in payload['sources'][0]['body']
        assert 'Spalte 10' in task
        return 'Laut Klassentabelle: A seit 02.03.2001, B seit 04.05.1999.'
    monkeypatch.setattr('backend.sources.paperless_document_text',read);monkeypatch.setattr('backend.main.generate',answer)
    monkeypatch.setattr('backend.main.plan_question',forbidden)
    for name in ('search_mail_context','search_document_context','poll_ntfy','relevant_smart_states'):monkeypatch.setattr(app.state.worker,name,forbidden)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Seit wann habe ich meinen Motorradführerschein und Autoführerschein?','share_external':True}).json()
        assert result['external_used'] and len(result['sources'])==1
        assert 'SECRET123' not in str(result)
