"""Stable question variants complement end-to-end source/answer tests in the full suite."""
import json
from pathlib import Path
import pytest
from backend.routing import route_question
from backend.planning import local_plan
from backend.public_data import needs_public_context

CASES=json.loads((Path(__file__).parent/'fixtures/question_regression.json').read_text())['cases']

@pytest.mark.parametrize('case',CASES,ids=lambda case:case['question'])
def test_question_domain_and_source_budget(case):
    question=case['question']
    assert list(needs_public_context(question))==case.get('public',[False,False])
    if case.get('public_only'):
        return  # Public context is selected independently of the private-source plan.
    route=route_question(question)
    if 'kind' in case:
        assert route and route['kind']==case['kind']
        assert 'plan' not in route
        if 'clarification' in case:assert route['clarification']==case['clarification']
        return
    plan=route['plan'] if route else local_plan(question)
    assert set(plan.sources)==set(case['sources'])
    if 'intent' in case:assert plan.intent==case['intent']
    if 'sender' in case:assert plan.sender==case['sender']
    if 'query' in case:assert any(case['query'].casefold() in query.casefold() for query in plan.queries)


def test_weather_followup_uses_user_context_without_assistant_contamination():
    history=[{'role':'user','text':'Wie wird das Wetter morgen?'},{'role':'assistant','text':'Prüfe auch deine Feiertage.'}]
    assert needs_public_context('Und übermorgen?',history)==(True,False)


def test_cost_followup_selects_documents_or_meter_without_all_sources():
    context=route_question('Wie hoch sind meine Stromkosten?')
    assert context['kind']=='clarify'
    assert set(route_question('Den monatlichen Abschlag',context)['plan'].sources)=={'email','paperless'}
    assert set(route_question('Den Kühlschrank diese Woche',context)['plan'].sources)=={'smart_home','paperless'}
