"""Durable search migration and job isolation, without downloads or external inference."""
import asyncio
import pytest
from backend.config import Settings
from backend.store import Store
from backend.worker import Worker
from backend.semantic import SemanticSearch


def item(version='1',body='Die Heizung wurde gewartet.'):
    return dict(id='mail:1',version=version,title='Service Rechnung',body=body,source='imap')


def test_vector_revision_and_legacy_backfill(tmp_path):
    store=Store(tmp_path);store.commit_item(item())
    with store.connect() as db:db.execute('DELETE FROM chunk_fingerprints')
    store.prepare_semantic_metadata()
    old=store.pending_semantic('test')[0]
    store.semantic_indexed('test',[old])
    assert store.semantic_counts('test')['remaining']==0
    assert store.semantic_evidence(old,'Heizung')
    store.commit_item(item('2','Neue Wartung der Heizung.'))
    assert store.semantic_evidence(old,'Heizung') is None
    assert store.semantic_counts('test')['remaining']==1
    assert store.obsolete_semantic('test')
    store.semantic_indexed('test',store.pending_semantic('test'))
    store.forget_semantic('test')
    assert Store(tmp_path).semantic_counts('test')['remaining']==1


def test_analysis_queue_survives_restart_and_rejects_old_completion(tmp_path):
    store=Store(tmp_path);store.commit_item(item(),'lokal');store.queue_analysis(item())
    job=Store(tmp_path).next_analysis();assert job['version']=='1'
    store.commit_item(item('2'),'neu lokal');store.queue_analysis(item('2'))
    store.finish_analysis(job,'veraltet')
    assert store.next_analysis()['version']=='2'
    assert all(event['reason']!='veraltet' for event in store.events())
    new=store.next_analysis();store.finish_analysis(new,'ausgewertet')
    assert store.next_analysis() is None
    assert len(store.events())==2


def test_slow_source_and_analysis_never_block_other_source(tmp_path,monkeypatch):
    worker=Worker(Settings(_env_file=None,data_dir=tmp_path,allow_external_llm=True),Store(tmp_path))
    async def scenario():
        blocked=asyncio.Event()
        async def poll(name):
            if name=='paperless':await blocked.wait()
            else:await worker.process(item())
        monkeypatch.setattr(worker,'poll_items',poll)
        slow=asyncio.create_task(worker.run_source('paperless'))
        await asyncio.sleep(0)
        await asyncio.wait_for(worker.run_source('imap'),1)
        assert worker.store.search('Heizung')
        assert worker.store.next_analysis()
        assert not slow.done()
        blocked.set();await slow
    asyncio.run(scenario())


def test_missing_model_falls_back_without_network(tmp_path,monkeypatch):
    store=Store(tmp_path);store.commit_item(item())
    worker=Worker(Settings(_env_file=None,data_dir=tmp_path),store)
    def forbidden(*args,**kwargs):raise AssertionError('download/inference forbidden')
    monkeypatch.setattr('backend.semantic.create_model',forbidden)
    hits=asyncio.run(worker.search_context('Heizung'))
    assert hits and worker.semantic.state['ready'] is False


def test_embedded_qdrant_filters_and_replaces_vectors(tmp_path,monkeypatch):
    # Exercise the actual local vector database with deterministic embeddings, no model download.
    import numpy as np
    store=Store(tmp_path);store.commit_item(item())
    class Model:
        def embed(self,texts,**kwargs):
            for text in texts:
                vector=np.zeros(384,dtype=np.float32);vector[0]=1;yield vector
    monkeypatch.setattr('backend.semantic.model_info',lambda _:dict(key='test'))
    monkeypatch.setattr('backend.semantic.create_model',lambda _:Model())
    search=SemanticSearch(Settings(_env_file=None,data_dir=tmp_path),store)
    try:
        assert search.index_batch()==1
        assert search.search('Heizung',sources=['paperless'])==[]
        assert search.search('Heizung',sources=['imap'])[0]['item_id']=='mail:1'
        store.commit_item(item('2','Neue Heizungswartung'))
        assert search.search('Heizung')==[]  # old vector never exposes newer content
        assert search.index_batch()==1
        assert search.search('Heizung')[0]['body']=='Neue Heizungswartung'
    finally:search.close()


def test_queued_advertising_respects_changed_context_and_consent(tmp_path,monkeypatch):
    store=Store(tmp_path)
    store.save_context({'projects':[{'name':'Heizung','keywords':['Heizung'],'active':True}]})
    worker=Worker(Settings(_env_file=None,data_dir=tmp_path,allow_external_llm=True),store)
    ad=item();ad['advertising']=True
    asyncio.run(worker.process(ad))
    assert store.next_analysis()['advertising'] is True
    worker.s.allow_external_llm=False
    assert asyncio.run(worker.analysis_one()) is False
    assert store.next_analysis()
    worker.s.allow_external_llm=True
    store.save_context({'projects':[]})
    async def forbidden(*args,**kwargs):raise AssertionError('Unrelated ad must remain local')
    monkeypatch.setattr('backend.worker.generate',forbidden)
    assert asyncio.run(worker.analysis_one()) is True
    assert store.next_analysis() is None


def test_chat_search_reuses_semantic_candidates_across_lexical_variants(tmp_path,monkeypatch):
    store=Store(tmp_path);worker=Worker(Settings(_env_file=None,data_dir=tmp_path),store)
    calls=[];lexical=[]
    monkeypatch.setattr(store,'search',lambda query,**kwargs:lexical.append(query) or [])
    monkeypatch.setattr(worker.semantic,'search',lambda query,sources:calls.append((query,sources)) or [])
    async def scenario():
        worker.search_session.set({'question':'Wie hoch sind meine Stromkosten?'})
        for query in ('Strom Tarif','Strom Abschlag','Strom Grundpreis'):
            await worker.search_context(query,source_filter=['paperless','imap'])
            await worker.search_context(query,strict=False,source_filter=['imap','paperless'])
        assert len(calls)==1 and calls[0][0]=='Wie hoch sind meine Stromkosten?'
        assert len(lexical)==6
        worker.search_session.set({'question':'Eine andere Frage'})
        await worker.search_context('Anderer Suchbegriff',source_filter=['imap','paperless'])
        assert len(calls)==2 and calls[-1][0]=='Eine andere Frage'
    asyncio.run(scenario())


def test_failed_vector_collection_initialization_is_not_published(tmp_path,monkeypatch):
    from qdrant_client import QdrantClient
    monkeypatch.setattr('backend.semantic.model_info',lambda _:dict(key='retry-key'))
    monkeypatch.setattr('backend.semantic.create_model',lambda _:object())
    original=QdrantClient.create_collection;calls=[]
    def create(self,*args,**kwargs):
        calls.append(1)
        if len(calls)==1:raise RuntimeError('synthetic initialization failure')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(QdrantClient,'create_collection',create)
    semantic=SemanticSearch(Settings(_env_file=None,data_dir=tmp_path),Store(tmp_path))
    with pytest.raises(RuntimeError):semantic.ensure()
    assert semantic.client is None
    try:
        assert semantic.ensure() and semantic.client is not None and len(calls)==2
    finally:semantic.close()


def test_background_failure_codes_do_not_expose_secrets():
    import imaplib,sqlite3
    from backend.worker import failure_code
    assert failure_code(imaplib.IMAP4.abort('private mailbox password'))=='imap_disconnected'
    assert failure_code(sqlite3.OperationalError('database is locked'))=='sqlite_busy'
    assert failure_code(RuntimeError('Storage folder private is already accessed by another instance'))=='vector_storage_locked'
