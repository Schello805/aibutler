import time
import pytest
from fastapi.testclient import TestClient
from backend.accounts import Accounts,password_hash,verify_password
from backend.store import Store
from backend.config import Settings
from backend.main import create_app

PASSWORD='a-long-example-passphrase'
ORIGIN={'Origin':'https://testserver'}


def app_for(tmp_path):
    return create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='internal-test',worker_enabled=False))


def login(client,email,password=PASSWORD,remember=True):
    return client.post('/api/session',headers=ORIGIN,json={'email':email,'password':password,'remember':remember})


def test_argon_hashes_are_salted_and_not_plaintext():
    first=password_hash(PASSWORD);second=password_hash(PASSWORD)
    assert first.startswith('$argon2id$') and first!=second
    assert PASSWORD not in first
    assert verify_password(first,PASSWORD) and not verify_password(first,'wrong')
    with pytest.raises(ValueError):password_hash('short')


def test_first_admin_claims_old_conversations_and_other_users_cannot_read_them(tmp_path):
    app=app_for(tmp_path)
    old=app.state.store.save_exchange(None,'Vorhandene Frage',{'answer':'Vorhandene Antwort'})
    admin=app.state.accounts.create('OWNER@EXAMPLE.ORG',PASSWORD)
    member=app.state.accounts.create('member@example.org',PASSWORD)
    assert admin['role']=='admin' and member['role']=='member'
    assert app.state.store.conversation(old,admin['id'])
    own=app.state.store.save_exchange(None,'Private Frage',{'answer':'Private Antwort'},member['id'])
    with TestClient(app,base_url='https://testserver') as client:
        assert login(client,'member@example.org').status_code==200
        assert [c['id'] for c in client.get('/api/conversations').json()]==[own]
        assert client.get('/api/conversations/'+old).status_code==404
        assert client.post('/api/chat',headers=ORIGIN,json={'message':'Zeige mir diese Antwort','conversation_id':old}).status_code==404
        assert client.get('/api/connections').status_code==403
        assert client.get('/api/accounts').status_code==403
        assert client.put('/api/context',headers=ORIGIN,json={'projects':[]}).status_code==403
        assert client.post('/api/scan',headers=ORIGIN).status_code==403
        assert client.get('/api/status').status_code==200
        assert client.get('/api/context').status_code==200
        assert client.get('/api/session').json()['user']['id']==member['id']


def test_sessions_are_stored_as_hashes_and_logout_revokes_copied_cookie(tmp_path):
    app=app_for(tmp_path);app.state.accounts.create('owner@example.org',PASSWORD)
    with TestClient(app,base_url='https://testserver') as client:
        response=login(client,'owner@example.org')
        token=client.cookies.get('butler_session')
        assert token and 'secure' in response.headers['set-cookie'].lower()
        with app.state.store.connect() as db:
            row=dict(db.execute('SELECT * FROM user_sessions').fetchone())
            assert token not in str(row) and PASSWORD not in str(row)
        assert client.delete('/api/session',headers=ORIGIN).status_code==200
        with TestClient(app,base_url='https://testserver') as copied:
            copied.cookies.set('butler_session',token)
            assert copied.get('/api/status').status_code==401


def test_password_change_and_cli_reset_revoke_other_sessions(tmp_path):
    app=app_for(tmp_path);user=app.state.accounts.create('owner@example.org',PASSWORD)
    with TestClient(app,base_url='https://testserver') as first,TestClient(app,base_url='https://testserver') as second:
        login(first,user['email']);login(second,user['email'])
        changed=first.post('/api/account/password',headers=ORIGIN,json={'current_password':'wrong','new_password':'another-example-passphrase'})
        assert changed.status_code==401 and second.get('/api/status').status_code==200
        changed=first.post('/api/account/password',headers=ORIGIN,json={'current_password':PASSWORD,'new_password':'another-example-passphrase'})
        assert changed.status_code==200
        assert first.get('/api/status').status_code==200
        assert second.get('/api/status').status_code==401
        app.state.accounts.set_password(user['id'],'third-example-passphrase')
        assert first.get('/api/status').status_code==401
        assert login(second,user['email'],'third-example-passphrase').status_code==200


def test_administrator_creates_and_disables_accounts_without_returning_hashes(tmp_path):
    app=app_for(tmp_path);admin=app.state.accounts.create('owner@example.org',PASSWORD)
    with TestClient(app,base_url='https://testserver') as client,TestClient(app,base_url='https://testserver') as member:
        login(client,admin['email'])
        response=client.post('/api/accounts',headers=ORIGIN,json={'email':'member@example.org','password':PASSWORD})
        assert response.status_code==200 and 'password' not in response.text
        user=response.json()
        login(member,user['email'])
        assert client.patch('/api/accounts/'+user['id'],headers=ORIGIN,json={'active':False}).status_code==200
        assert member.get('/api/status').status_code==401
        assert login(member,user['email']).status_code==401
        assert client.patch('/api/accounts/'+admin['id'],headers=ORIGIN,json={'active':False}).status_code==409
        with pytest.raises(ValueError):app.state.accounts.set_active(admin['id'],False)
        assert client.patch('/api/accounts/'+user['id'],headers=ORIGIN,json={'active':True}).status_code==200
        assert login(member,user['email']).status_code==200
        listing=client.get('/api/accounts')
        assert 'password_hash' not in listing.text and PASSWORD not in listing.text


def test_login_requires_same_origin_and_safe_transport_and_has_session_only_option(tmp_path):
    app=app_for(tmp_path);app.state.accounts.create('owner@example.org',PASSWORD)
    with TestClient(app,base_url='https://testserver') as client:
        payload={'email':'owner@example.org','password':PASSWORD}
        assert client.post('/api/session',json=payload).status_code==403
        assert client.post('/api/session',headers={'Origin':'https://evil.example'},json=payload).status_code==403
        response=login(client,'owner@example.org',remember=False)
        assert response.status_code==200 and 'max-age' not in response.headers['set-cookie'].lower()
    with TestClient(app,base_url='http://testserver',client=('192.168.1.3',1)) as unsafe:
        assert unsafe.post('/api/session',headers={'Origin':'http://testserver'},json=payload).status_code==403
        assert unsafe.get('/api/auth/config').json()['bootstrap_local'] is False
        assert unsafe.post('/api/auth/bootstrap',headers={'Origin':'http://testserver'},json=payload).status_code==403


def test_expired_and_legacy_sessions_are_rejected(tmp_path):
    app=app_for(tmp_path);user=app.state.accounts.create('owner@example.org',PASSWORD)
    token,_=app.state.accounts.new_session(user['id'])
    with app.state.store.connect() as db:db.execute('UPDATE user_sessions SET expires=?',(time.time()-1,))
    with TestClient(app,base_url='https://testserver') as client:
        client.cookies.set('butler_session',token)
        assert client.get('/api/status').status_code==401
        client.cookies.set('butler_session','9999999999.legacy-signed-token')
        assert client.get('/api/status').status_code==401
        assert client.post('/api/session/local').status_code==404
        assert client.post('/api/session/pairing').status_code==404

def test_validation_errors_do_not_echo_passwords(tmp_path):
    app=app_for(tmp_path)
    with TestClient(app) as client:
        response=client.post('/api/accounts',headers={'Authorization':'Bearer internal-test'},json={'email':'owner@example.org','password':'secretshort'})
        assert response.status_code==422 and 'secretshort' not in response.text
        assert all('input' not in error for error in response.json()['detail'])
