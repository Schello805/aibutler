"""Bounded answer deadlines that distinguish startup from an active text stream."""
import asyncio
from time import monotonic
from .intelligence import answer_sink

class AnswerTimeout(TimeoutError):
    def __init__(self,reason):
        super().__init__(reason);self.reason=reason

def timeout_message(error):
    if error.reason=='first_text':return 'Die KI hat noch keine Antwort geliefert. Bitte versuche es erneut.'
    if error.reason=='idle':return 'Die Antwort wurde unterbrochen, weil länger kein weiterer Text ankam. Bitte versuche es erneut.'
    return 'Die Antwort konnte nicht vollständig abgeschlossen werden. Bitte versuche es erneut.'

async def complete_answer(operation,*,first_text_timeout=20,idle_timeout=30,total_timeout=120):
    previous=answer_sink.get()
    if previous is None:
        # Legacy JSON clients cannot expose text progress. Preserve their provider API
        # behavior while allowing a reasonable complete-response timeout.
        return await asyncio.wait_for(operation,timeout=min(30,total_timeout))
    started=monotonic();last_text=started;seen=False
    def observe(chunk):
        nonlocal last_text,seen
        if chunk:last_text=monotonic();seen=True
        previous(chunk)
    token=answer_sink.set(observe)
    try:task=asyncio.create_task(operation)
    finally:answer_sink.reset(token)
    try:
        while not task.done():
            now=monotonic();deadline=min(started+total_timeout,last_text+idle_timeout if seen else started+first_text_timeout)
            if now>=deadline:
                reason='completion' if now>=started+total_timeout else 'idle' if seen else 'first_text'
                raise AnswerTimeout(reason)
            await asyncio.wait({task},timeout=deadline-now)
        return task.result()
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task,return_exceptions=True)
