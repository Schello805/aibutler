import httpx
import pytest
from backend.config import Settings
from backend import intelligence as ai

@pytest.mark.parametrize('code,delay,fragment',[('rate_limit_exceeded',7,'Sekunden'),('insufficient_quota',300,'Abrechnungslimit'),('project_spend_limit_exceeded',300,'Abrechnungslimit')])
def test_limit_reason_and_retry_after_are_safe(monkeypatch,code,delay,fragment):
    monkeypatch.setattr(ai.time,'monotonic',lambda:100)
    monkeypatch.setattr(ai,'_RATE_LIMITS',{})
    monkeypatch.setattr(ai,'_LIMIT_REASONS',{})
    settings=Settings(_env_file=None,llm_api_key='test-secret',llm_model='test')
    response=httpx.Response(429,headers={'Retry-After':'7'},json={'error':{'code':code,'message':'private response secret'}},request=httpx.Request('POST','https://api.openai.com/v1/responses'))
    with pytest.raises(ai.ProviderLimited) as error:ai.check_response(response,settings)
    assert fragment in str(error.value) and 'private response' not in str(error.value)
    assert ai._RATE_LIMITS[ai.limit_key(settings)]==100+delay
