"""Opt-in comparison with fixed synthetic tasks. No private source access."""
import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from .intelligence import generate_stream

CASES=[
 {'name':'kurze Antwort','question':'Nenne in einem Satz zwei Dinge für einen ersten Arbeitstag.','sources':[]},
 {'name':'Beleg lesen','question':'Wann ist der Testtermin?','sources':[{'source':'synthetic','body':'Einladung: Testtermin am 12.12.2030 um 09:30 Uhr.'}],'expected':['12.12.2030','09:30']},
 {'name':'Quellen verbinden','question':'Wann startet die Testperson und wo soll sie erscheinen?','sources':[{'source':'synthetic','body':'Testvertrag: Beginn am 01.02.2031.'},{'source':'synthetic','body':'Testmail: Am ersten Tag um 08:00 Uhr am Empfang melden.'}],'expected':['01.02.2031','08:00','Empfang']}
]

class BenchmarkInput(BaseModel):
    provider:Literal['openai','gemini']
    models:list[str]=Field(min_length=1,max_length=3)
    api_key:str=Field(default='',max_length=1000,repr=False)
    gemini_paid_confirmed:bool=False


def benchmark_router(settings,worker,auth):
    jobs={};tasks=set();handles={};start_lock=asyncio.Lock()
    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks):task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
    router=APIRouter(prefix='/api/benchmarks',dependencies=[Depends(auth)],lifespan=lifespan)
    def owner(request):return (request.state.user or {}).get('id','internal')
    @router.post('')
    async def start(value:BenchmarkInput,request:Request):
        async with start_lock:
            return await start_locked(value,request)
    async def start_locked(value,request):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}',name) for name in value.models):raise HTTPException(422,'Ungültige Modell-ID')
        if tasks:raise HTTPException(429,'Ein Modellvergleich läuft bereits.')
        key=value.api_key or (settings.llm_api_key if settings.llm_provider==value.provider else '')
        if not key:raise HTTPException(422,'API-Key für diesen Anbieter fehlt.')
        if value.provider=='gemini' and not (value.gemini_paid_confirmed or (settings.llm_provider=='gemini' and settings.gemini_paid_confirmed)):raise HTTPException(422,'Gemini-Vertragsbedingungen zuerst bestätigen.')
        identity=secrets.token_hex(16);actor=owner(request);state={'id':identity,'status':'running','provider':value.provider,'completed':0,'total':len(set(value.models))*len(CASES),'results':[]}
        if len(jobs)>=20:jobs.pop(next(iter(jobs)))
        jobs[identity]=(actor,state)
        await asyncio.to_thread(worker.store.save_sync_state,'benchmark:'+actor+':'+identity,state)
        async def run():
            try:
                for model in dict.fromkeys(value.models):
                    config=settings.model_copy(update={'llm_provider':value.provider,'llm_api_key':key,'llm_model':model,'allow_external_llm':True,'gemini_paid_confirmed':value.gemini_paid_confirmed or settings.gemini_paid_confirmed})
                    for case in CASES:
                        begin=time.monotonic();first=None;parts=[]
                        async def consume():
                            nonlocal first
                            async for chunk in generate_stream(config,'Beantworte die synthetische Testfrage knapp auf Deutsch. Nutze die Testbelege. Schreibe Daten als TT.MM.JJJJ und Uhrzeiten als HH:MM.',{key:val for key,val in case.items() if key not in ('name','expected')}):
                                if chunk:
                                    if first is None:first=time.monotonic()-begin
                                    parts.append(chunk)
                        row={'model':model,'case':case['name']}
                        try:
                            await asyncio.wait_for(consume(),20)
                            text=''.join(parts)
                            row.update(first_token_seconds=round(first,3) if first is not None else None,total_seconds=round(time.monotonic()-begin,3),ok=bool(text),fact_check=all(term.casefold() in text.casefold() for term in case.get('expected',[])),answer=text[:2000])
                        except Exception:row.update(ok=False,error='Modell nicht erreichbar, inkompatibel oder Zeitlimit erreicht.',total_seconds=round(time.monotonic()-begin,3))
                        state['results'].append(row);state['completed']+=1
                state['status']='done'
            except asyncio.CancelledError:state['status']='interrupted';raise
            finally:
                await asyncio.to_thread(worker.store.save_sync_state,'benchmark:'+actor+':'+identity,state)
        task=asyncio.create_task(run());tasks.add(task);handles[identity]=task
        def finished(task):
            tasks.discard(task);handles.pop(identity,None)
            if not task.cancelled():task.exception()
        task.add_done_callback(finished)
        return {'id':identity,'total':state['total']}
    @router.post('/{identity}/cancel')
    async def cancel(identity:str,request:Request):
        if identity not in jobs or jobs[identity][0]!=owner(request):raise HTTPException(404,'Vergleich nicht gefunden')
        task=handles.get(identity)
        if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
        return {'cancelled':bool(task)}

    @router.get('/{identity}')
    async def result(identity:str,request:Request):
        if not __import__('re').fullmatch(r'[a-f0-9]{32}',identity):raise HTTPException(404,'Vergleich nicht gefunden')
        actor=owner(request)
        if identity in jobs and jobs[identity][0]==actor:return jobs[identity][1]
        state=await asyncio.to_thread(worker.store.sync_state,'benchmark:'+actor+':'+identity)
        if not state:raise HTTPException(404,'Vergleich nicht gefunden')
        if state['status']=='running':state['status']='interrupted'
        return state
    return router
