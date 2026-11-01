from backend.store import Store
import pytest


def test_submitted_question_survives_interruption_and_is_visible_to_same_account(tmp_path):
    store=Store(tmp_path);turn=store.begin_turn(None,'Meine wichtige Frage','owner','request')
    second_device=Store(tmp_path)
    assert second_device.conversations('owner')[0]['id']==turn['conversation_id']
    assert second_device.conversation(turn['conversation_id'],'other') is None
    messages=second_device.conversation(turn['conversation_id'],'owner')['messages']
    assert messages[0]['text']=='Meine wichtige Frage' and messages[1]['payload']['pending']
    second_device.recover_pending_turns()
    recovered=store.conversation(turn['conversation_id'],'owner')['messages']
    assert len(recovered)==2 and recovered[0]['text']=='Meine wichtige Frage'
    assert recovered[1]['payload']['interrupted']


def test_answer_completes_existing_turn_without_duplicate_question(tmp_path):
    store=Store(tmp_path);turn=store.begin_turn(None,'Frage','owner','request')
    result={'answer':'Antwort','sources':[]}
    store.save_exchange(turn['conversation_id'],'Frage',result,'owner',request_id='request')
    store.save_exchange(turn['conversation_id'],'Frage',result,'owner',request_id='request')
    messages=store.conversation(turn['conversation_id'],'owner')['messages']
    assert [(row['role'],row['text']) for row in messages]==[('user','Frage'),('assistant','Antwort')]
    replay=store.begin_turn(None,'Frage','owner','request')
    assert replay['status']=='done' and replay['result']['answer']=='Antwort'
    with pytest.raises(ValueError):store.begin_turn(None,'Andere Frage','owner','request')
