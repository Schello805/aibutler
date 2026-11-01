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
