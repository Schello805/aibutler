import asyncio
import pytest
from backend.answer_wait import complete_answer, AnswerTimeout
from backend.intelligence import answer_sink


def run_stream(operation, **budgets):
    async def run():
        chunks=[]
        token=answer_sink.set(chunks.append)
        try:
            result=await complete_answer(operation(), **budgets)
            assert answer_sink.get()==chunks.append
            return result,chunks
        finally:
            answer_sink.reset(token)
    return asyncio.run(run())


def test_active_stream_can_outlive_initial_deadline():
    async def operation():
        for chunk in ['Hallo ', 'Michael.',' Fertig.']:
            answer_sink.get()(chunk)
            await asyncio.sleep(.025)
        return 'Hallo Michael. Fertig.'
    result,chunks=run_stream(operation,first_text_timeout=.02,idle_timeout=.1,total_timeout=.5)
    assert result==''.join(chunks)


@pytest.mark.parametrize('mode,reason',[('silent','first_text'),('stalled','idle'),('active','completion')])
def test_deadlines_cancel_and_join_provider(mode,reason):
    cleaned=[]
    async def operation():
        try:
            if mode=='stalled':answer_sink.get()('Anfang')
            while True:
                if mode=='active':answer_sink.get()('Text')
                await asyncio.sleep(.005)
        finally:cleaned.append(True)
    with pytest.raises(AnswerTimeout) as error:
        run_stream(operation,first_text_timeout=.03,idle_timeout=.04,total_timeout=.12)
    assert error.value.reason==reason and cleaned==[True]


def test_user_cancellation_stops_provider_and_restores_context():
    async def run():
        started=asyncio.Event();cleaned=[]
        async def operation():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:cleaned.append(True)
        sink=lambda chunk:None
        token=answer_sink.set(sink)
        try:
            task=asyncio.create_task(complete_answer(operation()))
            await started.wait();task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert cleaned==[True] and answer_sink.get() is sink
        finally:answer_sink.reset(token)
    asyncio.run(run())


def test_legacy_client_keeps_nonstreaming_provider_behavior():
    async def run():
        async def operation():
            assert answer_sink.get() is None
            await asyncio.sleep(.03)
            return 'Fertig'
        return await complete_answer(operation(),first_text_timeout=.01,total_timeout=.2)
    assert asyncio.run(run())=='Fertig'
