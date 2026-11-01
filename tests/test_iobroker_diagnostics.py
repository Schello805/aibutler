import httpx
import pytest
from backend.config import Settings
from backend.smarthome import iobroker_error

@pytest.mark.parametrize('kind,code,expected',[
 ('http',401,'Leserechte'),('http',404,'8081'),('http',503,'503'),
 ('connect',None,'LXC'),('timeout',None,'rechtzeitig'),('format',None,'Zustandsliste')])
def test_diagnostics_are_actionable_without_leaking_secrets(kind,code,expected):
    request=httpx.Request('GET','http://private-host/states?token=secret-value')
    if kind=='http':error=httpx.HTTPStatusError('secret-value',request=request,response=httpx.Response(code,request=request))
    elif kind=='connect':error=httpx.ConnectError('secret-value',request=request)
    elif kind=='timeout':error=httpx.ReadTimeout('secret-value',request=request)
    else:error=ValueError('secret-value')
    message=iobroker_error(error,Settings(_env_file=None,iobroker_url='http://private-host:8081'))
    assert expected in message
    assert 'secret-value' not in message and 'private-host' not in message
