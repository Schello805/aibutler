"""Live, read-only lighting questions with a deterministic answer and explicit coverage."""
import asyncio
import json
import math
import re
from urllib.parse import quote

import httpx
from .smarthome import state_inventory, utc_now

LIGHT_TERMS = re.compile(r'licht|lampe|leucht|light|lamp', re.I)
NON_LIGHT = re.compile(r'gong|signaltaster|status.?led|signal.?led|indicator|bewegungsmelder|motionsensor|lichtsensor', re.I)
CURRENT_QUESTION = re.compile(r'\b(an|aus|eingeschaltet|ausgeschaltet|leuchten|brennen|aktiv|status|gerade|aktuell)\b', re.I)
OTHER_QUESTION = re.compile(r'\b(gestern|vorgestern|wann|letzte[nsrm]?|früher|historisch|rechnung|dokument|unterlagen|bestell\w*|gekauft|mail\w*|kosten)\b', re.I)


def is_current_lighting_question(question):
    return bool(LIGHT_TERMS.search(question) and CURRENT_QUESTION.search(question) and not OTHER_QUESTION.search(question))


_QUERY_FILLERS=set('welche welcher welches welchen lichter licht lampe lampen leuchte leuchten beleuchtung light lights lamp lamps sind ist die das der den dem ein eine im in am auf bei von noch gerade aktuell an aus eingeschaltet ausgeschaltet brennen aktiv status bitte mir sag zeig heute jetzt alle auch wie viele und oder es denn wohl nicht immer insgesamt zuhause derzeit momentan jetzt schon meiner meine meinen mein unsere unseren ganzen haus'.split())

def normalized(value):
    return value.casefold().translate(str.maketrans({'ä':'ae','ö':'oe','ü':'ue','ß':'ss'}))


def entity_terms(question,rooms):
    ignored={normalized(word) for word in _QUERY_FILLERS}
    ignored.update(normalized(word) for room in rooms for word in re.findall(r'\w+',room))
    return [normalized(word) for word in re.findall(r'\w{3,}',question) if normalized(word) not in ignored]


def name_text(value, fallback):
    if isinstance(value, dict):
        value = value.get('de') or value.get('en') or fallback
    return str(value or fallback)[:300]


def ancestors(state_id):
    parts = state_id.split('.')
    return ['.'.join(parts[:i]) for i in range(1, len(parts) + 1)]


def names_for(obj, members):
    names = set()
    for state_id in (obj['id'], obj.get('alias_id')):
        if state_id:
            for parent in ancestors(state_id):
                names.update(members.get(parent, []))
    return sorted(names)


def clean_name(obj):
    return re.sub(r'(?:[.:](?:STATE|LEVEL|POWER|ON|BRI|BRIGHTNESS))$', '', obj['name'], flags=re.I).strip()


def actuator_kind(obj):
    if obj['id'].startswith(('system.','script.')) or re.search(r'(?:^|\.)scriptEnabled(?:\.|$)',obj['id'],re.I):
        return None
    role = str(obj.get('role') or '').lower()
    suffix = obj['id'].rsplit('.', 1)[-1].casefold()
    if role in ('switch','switch.light','switch.power','switch.socket') or role.startswith('switch.light.'):
        return 'power'
    if role.startswith('level.dimmer'):
        return 'dimmer'
    if role in ('state', '') and obj.get('value_type') == 'boolean' and suffix in ('state', 'on', 'power'):
        return 'power'
    return None


def boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.casefold() in ('true', 'false', 'on', 'off', '1', '0'):
        return value.casefold() in ('true', 'on', '1')
    return None


def state_is_on(state, kind):
    if not state or state.get('q', 0) not in (0, None) or state.get('ack') is False:
        return None
    value = state.get('val')
    if kind == 'power':
        return boolean(value)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        return None
    return value > 0


async def read_lights(settings, question, inventory=None, enums=None):
    result = {'checked': utc_now(), 'lights': [], 'fresh': False, 'truncated': False, 'diagnostics': []}
    if not settings.iobroker_url:
        return {**result, 'error': 'ioBroker ist noch nicht verbunden.'}, inventory, enums
    if settings.iobroker_api != 'simple':
        return {**result, 'error': 'Die direkte Lichtabfrage benötigt derzeit die ioBroker-Simple-API-Verbindung.'}, inventory, enums
    headers = {'Authorization': 'Bearer ' + settings.iobroker_token} if settings.iobroker_token else {}
    auth = httpx.BasicAuth(settings.iobroker_user, settings.iobroker_password or '') if settings.iobroker_user and not settings.iobroker_token else None
    async with httpx.AsyncClient(timeout=15, headers=headers, auth=auth, follow_redirects=False) as client:
        if inventory is None:
            inventory = await state_inventory(client, settings)
        if enums is None:
            try:
                async with client.stream('GET', settings.iobroker_url + '/objects', params={'type': 'enum'}) as response:
                    response.raise_for_status()
                    buffer = bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > 4 * 1024 * 1024:
                            raise ValueError('Enum inventory too large')
                raw = json.loads(buffer)
                if not isinstance(raw, dict) or 'error' in raw:
                    raise ValueError('Invalid enum response')
                enums = []
                for id, obj in raw.items():
                    if not isinstance(obj, dict) or not id.startswith(('enum.rooms.', 'enum.functions.')):
                        continue
                    common = obj.get('common') or {}
                    enums.append({'id': id, 'name': name_text(common.get('name'), id), 'members': [m for m in common.get('members', []) if isinstance(m, str)]})
            except Exception:
                enums = None
                result['diagnostics'].append('Raum-/Funktionszuordnungen waren nicht lesbar; nur eindeutig benannte Lichtaktoren wurden berücksichtigt.')
        rooms = {}; functions = {}
        for enum in enums or []:
            target = rooms if enum['id'].startswith('enum.rooms.') else functions if LIGHT_TERMS.search(enum['id'] + ' ' + enum['name']) else None
            if target is not None:
                for member in enum['members']:
                    target.setdefault(member, []).append(enum['name'])
        # Restrict to known room names mentioned explicitly in this question.
        requested_rooms = {e['name'] for e in enums or [] if e['id'].startswith('enum.rooms.') and re.search(r'(?<!\w)' + re.escape(e['name']) + r'(?!\w)', question, re.I)}
        extra_terms=entity_terms(question,requested_rooms)
        candidates = []
        for obj in inventory:
            kind = actuator_kind(obj)
            if not kind or NON_LIGHT.search(obj['name']):
                continue
            light_function = names_for(obj, functions)
            if not light_function and not LIGHT_TERMS.search(obj['name'] + ' ' + obj['id']):
                continue
            obj_rooms = names_for(obj, rooms)
            if requested_rooms and not requested_rooms.intersection(obj_rooms):
                continue
            haystack=normalized(obj['name']+' '+obj['id']+' '+' '.join(obj_rooms))
            if extra_terms and not all(term in haystack for term in extra_terms):
                continue
            candidates.append({**obj, 'kind': kind, 'rooms': obj_rooms, 'label': clean_name(obj)})
        result['scope'] = ', '.join(sorted(requested_rooms))
        result['truncated'] = len(candidates) > 300
        candidates = candidates[:300]
        availability = {}
        for obj in inventory:
            suffix = obj['id'].rsplit('.', 1)[-1].lower()
            if suffix not in ('unreach', 'unreachable', 'online', 'reachable', 'connected', 'available'):
                continue
            parent = obj['id'].rsplit('.', 1)[0]
            if suffix in ('unreach', 'unreachable') and parent.endswith('.0'):
                parent = parent[:-2]
            availability.setdefault(parent, []).append(obj['id'])
        ids = {obj['id'] for obj in candidates}
        for obj in candidates:
            known = []
            for parent in reversed(ancestors(obj.get('alias_id') or obj['id'])[:-1]):
                if parent in availability:
                    known = availability[parent]
                    break
            obj['availability'] = known
            ids.update(known)
        semaphore = asyncio.Semaphore(8)

        async def get(state_id):
            async with semaphore:
                try:
                    response = await client.get(settings.iobroker_url + '/get/' + quote(state_id, safe=''))
                    response.raise_for_status()
                    state = response.json()
                    return state_id, {k: state[k] for k in ('val', 'ack', 'q', 'ts') if k in state} if isinstance(state, dict) and 'val' in state else None
                except Exception:
                    return state_id, None
        values = dict(await asyncio.gather(*(get(id) for id in sorted(ids))))
        grouped = {}
        for obj in candidates:
            canonical = obj.get('alias_id') or obj['id']
            grouped.setdefault(canonical.rsplit('.', 1)[0], []).append(obj)
        lights = []
        for group, signals in grouped.items():
            # An explicit on/off signal wins over cached brightness (e.g. Hue on=false, bri=200).
            controls = [obj for obj in signals if obj['kind'] == 'power'] or signals
            readings = [state_is_on(values.get(obj['id']), obj['kind']) for obj in controls]
            on = readings[0] if readings and all(value is readings[0] for value in readings) else None
            for obj in controls:
                for state_id in obj['availability']:
                    state = values.get(state_id)
                    available = state_is_on(state, 'power')
                    if state_id.rsplit('.', 1)[-1].casefold() in ('unreach', 'unreachable') and available is not None:
                        available = not available
                    if available is not True:
                        on = None
            label = next((obj['label'] for obj in controls if obj['id'].startswith('alias.')), controls[0]['label'])
            lights.append({'id': controls[0]['id'], 'name': label, 'rooms': sorted({room for obj in signals for room in obj['rooms']}), 'on': on, 'value': (values.get(controls[0]['id']) or {}).get('val'), 'source_ts': (values.get(controls[0]['id']) or {}).get('ts')})
        result.update(lights=sorted(lights, key=lambda obj: obj['name'].casefold()), fresh=True, checked=utc_now())
        return result, inventory, enums


def light_answer(question, result):
    if result.get('error'):
        return result['error']
    lights = result['lights']
    if not lights:
        return 'Ich konnte keine passenden Licht-Schaltpunkte eindeutig zuordnen.'
    requested = False if re.search(r'\b(aus|ausgeschaltet)\b', question, re.I) and not re.search(r'\b(an|eingeschaltet)\b', question, re.I) else True
    if re.search(r'\bnicht\s+(aus|ausgeschaltet)\b',question,re.I):requested=True
    if re.search(r'\bnicht\s+(an|eingeschaltet)\b',question,re.I):requested=False
    matching = [obj for obj in lights if obj['on'] is requested]
    unknown = sum(obj['on'] is None for obj in lights)
    action = 'An' if requested else 'Aus'
    if matching:
        names = [obj['name'] for obj in matching]
        listing = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' und ' + names[-1]
        answer = action + (' ist: ' if len(names) == 1 else ' sind: ') + listing + '.'
    elif unknown == len(lights):
        answer = 'Die aktuellen Lichtzustände konnte ich gerade nicht zuverlässig lesen.'
    elif unknown or result.get('truncated') or result.get('diagnostics'):
        answer = 'Unter den sicher erfassten Lichtern ist keines ' + ('an.' if requested else 'aus.')
    else:
        answer = 'Alle ' + str(len(lights)) + ' erfassten Lichter sind ' + ('aus.' if requested else 'an.')
    if unknown:
        answer += ' Bei ' + str(unknown) + (' weiteren Licht ist' if unknown == 1 else ' weiteren Lichtern ist') + ' der Zustand unklar.'
    if result.get('truncated'):
        answer += ' Die Abfrage umfasst nur einen Teil der Lichtpunkte.'
    return answer
