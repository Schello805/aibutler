"""Only invented sample numbers. No personal identifiers or provider requests."""
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.store import Store
from backend.tax_evidence import tax_id_question,has_tax_identifier,tax_relevant

@pytest.mark.parametrize('question',['Wie lautet meine Steuer-ID?','Was ist meine Steuer ID?','Wo finde ich meine Steueridentifikationsnummer?','Kennst du meine steuerliche Identifikationsnummer?','Wie lautet meine IdNr?'])
def test_aliases_find_official_wording_in_the_first_local_search(tmp_path,question):
    store=Store(tmp_path)
    store.commit_item({'id':'letter','source':'paperless','version':'1','title':'Bundeszentralamt für Steuern','body':'Testperson. Ihre steuerliche Identifikationsnummer lautet: 98 765 432 109. Erfundene Testnummer.'})
    assert tax_id_question(question)
    assert [row['item_id'] for row in store.search(question,strict=False)]==['letter']

@pytest.mark.parametrize('text',['Umsatzsteuer-ID: DE999999999','USt-IdNr.: DE999999999','Steuernummer 123/456/78901','Wirtschafts-Identifikationsnummer DE999999999'])
def test_other_identifier_types_do_not_count_as_tax_id(text):
    assert not tax_relevant('Rechnung Badezimmer',text)
    assert not has_tax_identifier(text)


def app_for(tmp_path):return create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,semantic_enabled=False,chat_llm_enabled=True,paperless_url='https://paperless.example'))

def insert(store,identity,title,body):store.commit_item({'id':identity,'title':title,'body':body,'source':'paperless','version':'1'})

def fake_answer(calls):
    async def answer(settings,task,payload):
        calls.append(payload)
        assert [row['item_id'] for row in payload['sources']]==['id_letter']
        assert '98 765 432 109' in payload['sources'][0]['body']
        assert 'Zuordnung zur angefragten Person prüfen' in task
        return 'Im erfundenen Testbeleg steht die angefragte Identifikationsnummer.'
    return answer


def test_first_chat_finds_label_near_end_and_skips_live_when_evidence_is_sufficient(tmp_path,monkeypatch):
    from backend import main as module
    app=app_for(tmp_path);calls=[]
    insert(app.state.store,'id_letter','Identifikationsnummer Testperson','Bundeszentralamt für Steuern.\n'+'Allgemeine Testinformationen. '*1200+'\nIhre steuerliche Identifikationsnummer: 98 765 432 109. Erfundene Nummer.')
    insert(app.state.store,'bathroom','Rechnung Badezimmer','USt-IdNr.: DE999999999')
    async def forbidden(*args,**kwargs):raise AssertionError('No live search or planner needed')
    monkeypatch.setattr(module,'generate',fake_answer(calls));monkeypatch.setattr(module,'plan_question',forbidden)
    monkeypatch.setattr(app.state.worker,'search_document_context',forbidden)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Wie lautet meine Steuer-ID?','share_external':True}).json()
    assert result['external_used'] and len(calls)==1 and result['retrieval_mode']=='indexed'
    assert [row['id'] for row in result['sources']]==['id_letter']


def test_weak_cached_reference_automatically_searches_further_without_research_button(tmp_path,monkeypatch):
    from backend import main as module
    app=app_for(tmp_path);answers=[];reads=[]
    insert(app.state.store,'weak','Steuer-ID Identifikationsnummer nachreichen','Bitte die steuerliche Identifikationsnummer mitteilen. Hier ist keine Nummer enthalten.')
    async def search(queries):
        reads.append(queries)
        insert(app.state.store,'id_letter','BZSt Testperson','Ihre steuerliche Identifikationsnummer: 98 765 432 109. Erfundene Testnummer.')
    monkeypatch.setattr(app.state.worker,'search_document_context',search)
    monkeypatch.setattr(module,'generate',fake_answer(answers))
    with TestClient(app) as client:
        first=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Wie lautet meine Steuer-ID?','share_external':True}).json()
        second=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Wie lautet meine Steuer ID?','share_external':True}).json()
    assert first['external_used'] and second['external_used']
    assert len(reads)==1 and len(answers)==2
    assert first['retrieval_mode']=='live_enriched' and second['retrieval_mode']=='indexed'

@pytest.mark.parametrize('text',['Kunden-IdNr.: 98 765 432 109','Steuer-ID fehlt. Telefon: 98765432109'])
def test_customer_or_telephone_numbers_do_not_make_local_evidence_sufficient(text):
    assert not has_tax_identifier(text)
