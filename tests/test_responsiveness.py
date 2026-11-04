"""Readers and the HTTP loop remain usable during background database work."""
import asyncio
import threading
from backend.config import Settings
from backend.store import Store
from backend.worker import Worker


def test_archive_writer_does_not_block_readers(tmp_path):
    store=Store(tmp_path)
    store.commit_item(dict(id='a',version='1',source='imap',title='Heizung',body='Wartung'))
    with store.connect() as writer:
        writer.execute("UPDATE items SET title='Noch nicht freigegebene Änderung' WHERE id='a'")
        # A second connection must see the last committed snapshot even with an open writer.
        with store.connect() as reader:
            reader.execute('PRAGMA busy_timeout=100')
            assert reader.execute("SELECT title FROM items WHERE id='a'").fetchone()[0]=='Heizung'


def test_slow_index_commit_does_not_block_event_loop(tmp_path,monkeypatch):
    worker=Worker(Settings(_env_file=None,data_dir=tmp_path),Store(tmp_path))
    entered=threading.Event();release=threading.Event()
    original=worker.store.commit_item
    def delayed(*args,**kwargs):
        entered.set();assert release.wait(2);return original(*args,**kwargs)
    monkeypatch.setattr(worker.store,'commit_item',delayed)
    async def scenario():
        task=asyncio.create_task(worker.process(dict(id='a',version='1',source='imap',title='Heizung',body='Wartung')))
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.005)
            assert entered.is_set()
            assert not task.done()  # the event loop ran while the disk operation was still waiting
        finally:release.set();await task
    asyncio.run(scenario())


def test_paperless_progress_write_cannot_stall_http_or_session(tmp_path,monkeypatch):
    import httpx
    import backend.worker as module
    from backend.main import create_app
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    worker=app.state.worker;entered=threading.Event();release=threading.Event()
    original=worker.store.save_sync_state
    def blocked(*args):
        entered.set();assert release.wait(3);return original(*args)
    async def documents(*args):return []
    monkeypatch.setattr(worker.store,'save_sync_state',blocked)
    monkeypatch.setattr(module,'read_paperless',documents)
    async def scenario():
        polling=asyncio.create_task(worker.poll_items('paperless'))
        try:
            assert await asyncio.to_thread(entered.wait,1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                response=await asyncio.wait_for(client.get('/api/session',headers={'Authorization':'Bearer test'}),.5)
                assert response.status_code==200
                assert not polling.done()  # HTTP responded while the database write is still blocked.
                assert (await client.get('/healthz')).status_code==200
        finally:release.set();await polling
    asyncio.run(scenario())


def test_icloud_snapshot_write_yields_to_event_loop(tmp_path,monkeypatch):
    from backend.icloud import PersonalSources,DAV
    store=Store(tmp_path)
    personal=PersonalSources(Settings(_env_file=None,data_dir=tmp_path,icloud_contacts_enabled=True,icloud_contacts_user='test@example.invalid',icloud_contacts_password='synthetic'),store)
    entered=threading.Event();release=threading.Event()
    original=store.save_dav_snapshot
    def blocked(*args):entered.set();assert release.wait(3);return original(*args)
    async def read(*args):return {'records':[]}
    monkeypatch.setattr(store,'save_dav_snapshot',blocked)
    monkeypatch.setattr(DAV,'read',read)
    async def scenario():
        task=asyncio.create_task(personal.refresh('contacts'))
        try:
            assert await asyncio.to_thread(entered.wait,1)
            await asyncio.sleep(.01)
            assert not task.done()
        finally:release.set();await task
    asyncio.run(scenario())
