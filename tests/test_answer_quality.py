"""Regression scenarios use synthetic documents only, with no provider calls."""
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Settings
from backend.store import Store
from backend.planning import local_plan,complete_plan,RetrievalPlan


def record(store,identifier,title,body,source='paperless',date='2026-09-03',**metadata):
    store.commit_item({'id':identifier,'version':'1','title':title,'body':body,'source':source,'document_date':date,**metadata})


def ask(app,question,external=False):
    with TestClient(app) as client:
        response=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':question,'share_external':external})
        assert response.status_code==200
        return response.json()


def app_for(path,**kwargs):
    return create_app(Settings(_env_file=None,data_dir=path,api_token='test',worker_enabled=False,**kwargs))


def test_new_maintenance_invoice_beats_older_exact_date_and_unrelated_news(tmp_path):
    app=app_for(tmp_path);store=app.state.store
    record(store,'old','Heizungswartung 2024','Wartung durchgeführt am 18.04.2024.',date='2024-04-20')
    record(store,'new','Solarfocus Service Rechnung Wartung 2026','Rechnung über die Wartung des Pelletkessels.')
    record(store,'spam','Newsletter Heizungswartung Jobs','Heizung Service Wartung täglich.',source='imap',date='2026-10-31')
    result=ask(app,'Wann war die letzte Wartung der Heizung?')
    assert 'wahrscheinlich im September 2026' in result['answer']
    assert 'spam' not in [source['id'] for source in result['sources']]
    assert any(source['id']=='new' for source in result['sources'])


def test_relaxed_search_keeps_maintenance_relevance_filter(tmp_path):
    store=Store(tmp_path)
    record(store,'spam','Newsletter Heizung','Heizung Service Wartung Jobangebote.')
    assert not store.search('Heizung Wartung',strict=False)


def test_recent_evidence_is_selected_before_candidate_limit(tmp_path):
    store=Store(tmp_path)
    for n in range(65):
        record(store,str(n),'Heizung Wartung','Heizung Wartung '*30,date=f'2020-01-{n%28+1:02}')
    record(store,'latest','Heizung Wartung','Leistungsdatum: 03.09.2026.')
    hits=store.search('Heizung Wartung',recent=True)
    assert hits[0]['item_id']=='latest'


def test_execution_date_after_first_ten_thousand_characters_is_available(tmp_path):
    app=app_for(tmp_path)
    record(app.state.store,'long','Rechnung Heizungswartung','Wartung der Heizung\n'+('Allgemeine Vertragsbedingungen. '*800)+'\nLeistungsdatum: Wartung durchgeführt am 03.09.2026.')
    result=ask(app,'Wann war die letzte Heizungswartung?')
    assert '03.09.2026' in result['answer']
    assert '03.09.2026' in result['sources'][0]['excerpt']
    assert len(result['sources'][0]['excerpt'])<=10000


def test_package_question_combines_mail_and_bell_even_if_ai_planner_fails(tmp_path,monkeypatch):
    import backend.main as main
    app=app_for(tmp_path,chat_llm_enabled=True);store=app.state.store;called=[]
    record(store,'delivery','Paket XYZ ist in Zustellung','Paket XYZ wird heute zugestellt.',source='imap')
    record(store,'bell','Klingel','Jemand hat um 12:30 geklingelt.',source='ntfy')
    record(store,'spam','Paket Newsletter Sonderangebot','Klingel Rabatt Paket XYZ',source='ntfy')
    async def failed(*args):raise RuntimeError('planner unavailable')
    async def ntfy():called.append('ntfy')
    async def answer(settings,task,payload):
        assert {'imap','ntfy'}<={hit['source'] for hit in payload['sources']}
        assert not any(hit['item_id']=='spam' for hit in payload['sources'])
        assert 'allein beweist keine Paketzustellung' in task
        return 'Es ist in Zustellung; die Klingelmeldung bestätigt noch keine Übergabe.'
    monkeypatch.setattr(main,'plan_question',failed);monkeypatch.setattr(main,'generate',answer)
    monkeypatch.setattr(app.state.worker,'poll_ntfy',ntfy)
    result=ask(app,'Ist Paket XYZ schon da?',True)
    assert called==['ntfy'] and result['external_used'] and not result['query_planned_by_ai']
    assert result['answer'].startswith('Es ist in Zustellung')


def test_latest_purchase_excludes_shipping_updates_and_advertising(tmp_path):
    app=app_for(tmp_path);store=app.state.store
    record(store,'order','Bestellt: Temperatursensor','* Temperatursensor\nGesamtbetrag: 19,99 EUR',source='imap',date='2026-09-01')
    record(store,'shipping','Versendet: Bestellbestätigung','Bestellnummer: 123',source='imap',date='2026-10-31')
    record(store,'ads','Newsletter: Bestellbestätigung Rabatt','Kaufbeleg Bestellt Rabatt',source='imap',date='2026-10-31')
    result=ask(app,'Was habe ich zuletzt gekauft?')
    assert 'Temperatursensor' in result['answer']
    assert [source['id'] for source in result['sources']]==['order']


def test_person_question_does_not_confuse_mentions_with_sender(tmp_path):
    app=app_for(tmp_path);store=app.state.store
    record(store,'sender','Logo korrigieren','<p>Hallo Michael,</p><p>Bitte das orange Logo korrigieren.</p><p>Am 01.01.2024 schrieb Michael:</p><p>Bitte Rechnung bezahlen.</p>',source='imap',sender_name='Tanja',sender_address='tanja@example.org',sent_at='2026-09-01')
    record(store,'mention','Tanja','Tanja hat den Vertrag gekündigt.',source='imap',date='2026-10-31',sender_name='Other',sender_address='other@example.org',sent_at='2026-10-31')
    result=ask(app,'Was wollte Tanja zuletzt von mir?')
    assert 'Logo korrigieren' in result['answer'] and 'Rechnung bezahlen' not in result['answer']
    assert [source['id'] for source in result['sources']]==['sender']


def test_incomplete_package_plan_reserves_event_query_and_sources():
    plan=complete_plan(RetrievalPlan(queries=['Paket XYZ','Sendung XYZ','Lieferung XYZ'],sources=['paperless']),'Ist Paket XYZ schon da?')
    assert {'email','ntfy'}<=set(plan.sources)
    assert 'Klingel' in plan.queries
    assert plan.recent
    assert local_plan('Wann hat es zuletzt geklingelt?').sources==['ntfy']


def test_cancelled_or_explicitly_unperformed_maintenance_has_no_exact_date():
    from backend.retrieval import maintenance_facts
    assert maintenance_facts({'title':'Stornierte Rechnung Heizungswartung','body':'Wartung durchgeführt am 03.09.2026.','document_date':'2026-09-03'})==[]
    assert maintenance_facts({'title':'Heizungswartung','body':'Wartung nicht durchgeführt am 03.09.2026.'})==[]


def test_german_purchase_total_retains_thousands_separator():
    from backend.retrieval import purchase_answer
    answer=purchase_answer([{'title':'Bestellt: Sensor','body':'* Sensor\nGesamtbetrag: 1.234,56 EUR'}])
    assert '1.234,56 €' in answer and '1,234,56' not in answer


@pytest.mark.parametrize('question',['Wie hoch ist der aktuelle Stromtarif?','Wie hoch ist der Strompreis pro Kilowattstunde?'])
def test_simple_energy_costs_use_local_plan_and_both_document_sources(tmp_path,monkeypatch,question):
    import backend.main as main
    app=app_for(tmp_path,chat_llm_enabled=True)
    record(app.state.store,'tariff','Strom Arbeitspreis Grundpreis','Strom Tarif Arbeitspreis 30 Cent pro kWh, Grundpreis 10 Euro monatlich.')
    record(app.state.store,'mail','Strom Preisänderung Tarif','Strom Tarif Preisänderung ab November: neuer Grundpreis.',source='imap')
    called=[]
    async def planner(*args,**kwargs):called.append('planner');raise AssertionError('No planner needed')
    async def answer(settings,task,payload):
        called.append('answer')
        assert {'imap','paperless'}<={row['source'] for row in payload['sources']}
        return 'Laut Tarif 30 Cent je Kilowattstunde und 10 Euro Grundpreis pro Monat.'
    monkeypatch.setattr(main,'plan_question',planner);monkeypatch.setattr(main,'generate',answer)
    result=ask(app,question,True)
    assert result['external_used'] and not result['query_planned_by_ai']
    assert called==['answer']
