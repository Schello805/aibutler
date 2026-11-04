import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.evidence_quality import review,read_sections,effective_date
from backend.fast_chat import compact_payload
from backend.main import create_app
from backend.quality_eval import evaluate_case
from backend.store import Store
from backend.sources import paperless_selected_document

FIXTURE=json.loads((Path(__file__).parent/'fixtures/answer_quality.json').read_text())

@pytest.mark.parametrize('case',FIXTURE['cases'],ids=lambda case:case['name'])
def test_quality_matrix(tmp_path,case):
    report,_=evaluate_case(tmp_path,case,FIXTURE['as_of'])
    assert report['passed'],report

def test_contract_reader_preserves_clause_conditions_and_does_not_mutate_originals(tmp_path):
    store=Store(tmp_path);body='Sielaff Testvertrag.\n'+'Andere Bestimmungen. '*2000+'\n§ 12 Urlaub\nDer jährliche Urlaub beträgt 31 Arbeitstage pro Kalenderjahr bei einer Fünftagewoche. Bei Teilzeit ist entsprechend umzurechnen.'
    store.commit_item({'id':'contract','source':'paperless','title':'Sielaff Arbeitsvertrag','body':body,'version':'1'})
    original={'item_id':'contract','source':'paperless','title':'Sielaff Arbeitsvertrag','body':'Titel und Anfang ohne Urlaubsklausel.'}
    rows,notes=review(store,'Wie viele Urlaubstage habe ich bei sielaff?',[original],date(2026,11,4))
    assert rows and '31 Arbeitstage' in rows[0]['body'] and 'Teilzeit' in rows[0]['body']
    assert original['body']=='Titel und Anfang ohne Urlaubsklausel.'
    assert store.full_evidence(['contract'])['contract']['body']==body
    payload=compact_payload({'question':'Wie viel Urlaub?','sources':rows,'evidence_review':notes})
    assert 'Fünftagewoche' in payload['sources'][0]['body']
    assert len(payload['sources'][0]['body'])<=6500

def test_strong_amendment_date_wins_over_referenced_contract_start():
    text='Arbeitsverhältnis beginnt am 01.02.2020. Mit Wirkung zum 01.01.2027 ändert dieser Nachtrag den Urlaub.'
    assert effective_date(text)==date(2027,1,1)
    assert effective_date(text+' Gilt ab 01.02.2027 eine andere Regelung.') is None

def test_licence_privacy_path_is_not_expanded(tmp_path):
    store=Store(tmp_path)
    hit={'item_id':'license','source':'paperless','title':'Führerschein','body':'A 01.01.2001'}
    assert review(store,'Führerschein und Urlaubstage',[hit],date(2026,11,4))==([hit],{})

def test_missing_document_read_keeps_current_origin_and_does_not_download_original(monkeypatch,tmp_path):
    settings=Settings(_env_file=None,data_dir=tmp_path,paperless_url='https://paperless.example',paperless_token='private')
    instance=hashlib.sha256(b'https://paperless.example/').hexdigest()[:12];calls=[]
    def handle(request):
        calls.append(request)
        assert str(request.url)=='https://paperless.example/api/documents/42/'
        return httpx.Response(200,json={'title':'Testvertrag','content':'§ Urlaub: jährlich 31 Arbeitstage.'})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    row=asyncio.run(paperless_selected_document(settings,'paperless:'+instance+':42'))
    assert '31' in row['body'] and len(calls)==1
    with pytest.raises(ValueError):asyncio.run(paperless_selected_document(settings,'paperless:other:42'))
    assert len(calls)==1

def test_chat_sends_selected_contract_section_and_validity_hints_in_one_answer_call(tmp_path,monkeypatch):
    from backend import main as module
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    body='Sielaff Testvertrag. Arbeitsverhältnis beginnt am 01.01.2025.\n'+'Andere Klauseln. '*2000+'\nUrlaubsanspruch: jährlich 31 Arbeitstage pro Kalenderjahr bei einer Fünftagewoche. Bei Teilzeit wird anteilig umgerechnet.'
    app.state.store.commit_item({'id':'contract','source':'paperless','title':'Arbeitsvertrag Sielaff','body':body,'version':'1'})
    app.state.store.commit_item({'id':'wrong','source':'paperless','title':'Arbeitsvertrag Andere Firma','body':'Der jährliche Urlaubsanspruch beträgt 49 Arbeitstage pro Kalenderjahr.','version':'1'})
    calls=[]
    async def answer(settings,task,payload):
        calls.append(payload)
        assert 'Dokument-/Importdatum ist kein Leistungs- oder Gültigkeitsdatum' in task
        assert payload['evidence_review']['topic']=='leave'
        assert [row['item_id'] for row in payload['sources']]==['contract']
        compact=compact_payload(payload)
        assert '31 Arbeitstage' in compact['sources'][0]['body'] and 'Teilzeit' in compact['sources'][0]['body']
        return 'Im synthetischen Testvertrag stehen 31 Arbeitstage bei einer Fünftagewoche.'
    async def forbidden(*args):raise AssertionError('No additional planner request')
    monkeypatch.setattr(module,'generate',answer);monkeypatch.setattr(module,'plan_question',forbidden)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Wie viele Urlaubstage habe ich bei Sielaff?','share_external':True}).json()
        assert result['external_used'] and len(calls)==1
        assert '31 Arbeitstage' in result['sources'][0]['excerpt']
        assert 'wrong' not in [row['id'] for row in result['sources']]

def test_tariff_comparison_distinguishes_tax_and_payment_basis(tmp_path):
    store=Store(tmp_path)
    docs=[('a','Arbeitspreis 30 Cent netto je kWh.'),('b','Arbeitspreis 36 Cent netto je kWh.'),('c','Arbeitspreis 35 Cent brutto je kWh.')]
    for identity,body in docs:store.commit_item({'id':identity,'title':'Stromtarif','body':body,'version':'1','source':'paperless'})
    hits=[{'item_id':identity,'title':'Stromtarif','body':body,'source':'paperless'} for identity,body in docs]
    _,notes=review(store,'Wie hoch ist der aktuelle Stromtarif?',hits,date(2026,11,4))
    assert len(notes['conflicts'])==1
    assert {claim['source_id'] for claim in notes['conflicts'][0]['claims']}=={'a','b'}

def test_selected_empty_contract_is_read_without_pdf_download_or_extra_llm_call(tmp_path,monkeypatch):
    from backend import main as module
    from backend import sources
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True,paperless_url='https://paperless.example')
    app=create_app(settings);instance=hashlib.sha256(b'https://paperless.example/').hexdigest()[:12];identity='paperless:'+instance+':42'
    app.state.store.commit_item({'id':identity,'title':'Sielaff Arbeitsvertrag','body':'','version':'1','source':'paperless'})
    reads=[];answers=[]
    async def read(s,item_id):
        reads.append(item_id)
        return {'id':item_id,'title':'Sielaff Arbeitsvertrag','body':'Der jährliche Urlaubsanspruch beträgt 31 Arbeitstage pro Kalenderjahr bei einer Fünftagewoche.','version':'2','source':'paperless'}
    async def answer(s,task,payload):answers.append(payload);assert '31 Arbeitstage' in payload['sources'][0]['body'];return 'Im Testvertrag stehen 31 Arbeitstage.'
    monkeypatch.setattr(sources,'paperless_selected_document',read);monkeypatch.setattr(module,'generate',answer)
    with TestClient(app) as client:
        for _ in range(2):
            response=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Wie viele Urlaubstage habe ich bei Sielaff?','share_external':True})
            assert response.status_code==200 and response.json()['external_used']
    assert reads==[identity] and len(answers)==2

def test_table_of_contents_and_numbered_headers_do_not_hide_late_entitlement():
    body='Testvertrag Sielaff.\n'+('§ 7.1 Urlaub: siehe Kapitel 12.4.\nAndere Bestimmungen.\n'*500)+'\n§ 12 Erholungsurlaub\nDer jährliche Urlaubsanspruch beträgt 31 Arbeitstage pro Kalenderjahr. Bei einer Fünftagewoche.'
    excerpt=read_sections(body,'Wie viele Urlaubstage habe ich bei Sielaff?')
    assert '31 Arbeitstage' in excerpt and 'Fünftagewoche' in excerpt

@pytest.mark.parametrize('value',['2,5','-5'])
def test_fractional_or_negative_days_do_not_become_a_positive_annual_total(value):
    from backend.evidence_quality import leave_claims
    assert leave_claims('Der jährliche Urlaubsanspruch beträgt '+value+' Arbeitstage.')==[]
