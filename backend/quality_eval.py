"""Repeatable evidence checks over synthetic cases. No external calls in this module."""
from datetime import date
from time import perf_counter
from .store import Store
from .planning import local_plan
from .retrieval import evidence_recency
from .evidence_quality import review
from .fast_chat import compact_payload


def evaluate_case(root,case,as_of):
    store=Store(root)
    for doc in case.get('documents',[]):
        row={**doc,'source':doc.get('source','paperless'),'version':'1'}
        row['body']=doc['body']+('Allgemeine Testbedingungen ohne individuelle Ansprüche.\n'*doc.get('filler',0))+doc.get('tail','')
        store.commit_item(row)
    start=perf_counter();checks={}
    if case.get('control'):
        from .controls import ControlService
        from .config import Settings
        settings=Settings(_env_file=None,data_dir=root,iobroker_control_enabled=True,iobroker_controls='[{"name":"Rollo Testzimmer","kind":"blind","state_id":"demo.0.blind"}]')
        result=ControlService(settings,store).plan(case['question'],'synthetic')
        checks['local_device_proposal']=bool(result and result.get('control_action') and result.get('query_kind')=='device_control' and not result.get('sources') and not result.get('external_used'))
        return {'name':case['name'],'passed':all(checks.values()),'checks':checks,'seconds':round(perf_counter()-start,4)},None
    plan=local_plan(case['question']);hits=[]
    if plan.intent=='person':hits=store.sender_messages(plan.sender)
    else:
        for query in plan.queries:
            hits.extend(store.search(query,strict=True,recent=plan.recent))
        hits=list({row['item_id']:row for row in hits}.values())
        if plan.recent:hits.sort(key=evidence_recency,reverse=True)
    hits,notes=review(store,case['question'],hits,date.fromisoformat(as_of))
    payload=compact_payload({'question':case['question'],'retrieval_queries':plan.queries,'sources':hits,'evidence_review':notes,'current_date':as_of})
    expected=case['expected'];ids=[row['item_id'] for row in payload['sources']];text='\n'.join(row['body'] for row in payload['sources'])
    if expected.get('primary'):checks['primary_source']=bool(ids and ids[0]==expected['primary'])
    checks['irrelevant_sources_excluded']=not set(ids).intersection(expected.get('excluded',[]))
    checks['critical_sections_preserved']=all(term in text for term in expected.get('section_terms',[]))
    checks['conflicts_reported']=bool(notes.get('conflicts'))==expected.get('conflicts',False)
    for identity in expected.get('future',[]):checks['future_'+identity]=any(note['source_id']==identity and note['future'] for note in notes.get('documents',[]))
    checks['bounded_model_context']=len(text)<=30000
    return {'name':case['name'],'passed':all(checks.values()),'checks':checks,'sources':ids,'seconds':round(perf_counter()-start,4)},payload
