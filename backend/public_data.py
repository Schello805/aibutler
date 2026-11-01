"""Opt-in public context. Never sends chat text, mail or documents to data providers."""
import asyncio
from collections import deque
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import re
import time
import httpx
from pydantic import BaseModel,Field,field_validator

class PublicConfig(BaseModel):
    weather_enabled:bool=False
    holidays_enabled:bool=False

class DeviceLocation(BaseModel):
    latitude:float=Field(ge=-90,le=90,allow_inf_nan=False)
    longitude:float=Field(ge=-180,le=180,allow_inf_nan=False)
    country:str=Field(default='',pattern=r'^(?:[A-Z]{2})?$')
    subdivision:str=Field(default='',pattern=r'^(?:[A-Z]{2}-[A-Z0-9]{1,6})?$')
    label:str=Field(default='',max_length=100)
    timezone:str=Field(default='Europe/Berlin',max_length=80)
    @field_validator('timezone')
    @classmethod
    def valid_zone(cls,value):
        try:ZoneInfo(value)
        except (ValueError,KeyError):raise ValueError('Ungültige Zeitzone')
        return value
    @field_validator('latitude','longitude')
    @classmethod
    def coarse(cls,value):return round(value,2)


def outdoor_question(question):
    explicit=bool(re.search(r'draußen|draussen|im freien|gartenarbeit|außen.{0,15}arbeit|aussen.{0,15}arbeit',question,re.I))
    project=bool(re.search(r'rasen.{0,3}mäh|rasenmaeh|hühnerstall|huehnerstall|zaun|terrasse',question,re.I))
    planning=bool(re.search(r'morgen|heute|kann ich|soll ich|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag',question,re.I))
    return explicit or (project and planning)


def needs_public_context(question,history=()):
    weather=outdoor_question(question) or bool(re.search(r'wetter|regne|regen|temperatur|frost|sonnig|windig',question,re.I))
    if not outdoor_question(question) and not re.search(r'wetter',question,re.I) and re.search(r'wohnzimmer|schlafzimmer|thermostat|heizung|gefrier|kühlschrank|kuehlschrank|innenraum',question,re.I):weather=False
    holiday=bool(re.search(r'feiertag|werktag|brückentag|brueckentag',question,re.I)) or outdoor_question(question)
    if len(question.split())<=6 and re.match(r'^(?:und\b|am\b|morgen\b|heute\b|übermorgen\b|wie (?:ist|wäre) es (?:mit|am)|kann ich (?:auch|dann))',question,re.I) and re.search(r'morgen|heute|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag',question,re.I):
        prior=[message['text'] for message in history if message.get('role','user')=='user']
        if prior:
            prior_weather,prior_holidays=needs_public_context(prior[-1])
            weather=weather or prior_weather;holiday=holiday or prior_holidays
    return weather,holiday


def target_day(question,today):
    if re.search(r'wochenende|nächste.{0,4}woche|naechste.{0,4}woche|nächste.{0,4}monat',question,re.I):return None
    if re.search(r'übermorgen|uebermorgen',question,re.I):return today+timedelta(days=2)
    if re.search(r'\bmorgen\b',question,re.I):return today+timedelta(days=1)
    match=re.search(r'\b(20\d{2}-\d{2}-\d{2})\b',question)
    if match:
        try:return date.fromisoformat(match[1])
        except ValueError:return None
    match=re.search(r'\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b',question)
    if match:
        try:return date(int(match[3]),int(match[2]),int(match[1]))
        except ValueError:return None
    for number,day in enumerate(['montag','dienstag','mittwoch','donnerstag','freitag','samstag','sonntag']):
        if re.search(r'\b'+day+r'\b',question,re.I):
            distance=(number-today.weekday())%7
            if distance==0 and re.search(r'nächsten|naechsten|kommenden',question,re.I):distance=7
            return today+timedelta(days=distance)
    return today


class PublicData:
    def __init__(self,settings,store):
        self.settings,self.store=settings,store
        self.locks={'weather':asyncio.Lock(),'holidays':asyncio.Lock(),'geocoding':asyncio.Lock()}
        self.failures={}
        self.requests={name:deque() for name in self.locks}

    async def get_json(self,url,params=None):
        # Fixed HTTPS origins only, no redirects, bounded responses and no inherited proxy.
        if url not in ('https://api.open-meteo.com/v1/forecast','https://geocoding-api.open-meteo.com/v1/search') and not re.fullmatch(r'https://(?:nagerholidays.com/api/v4/Holidays/[A-Z]{2}/20\d{2}|date.nager.at/api/v3/PublicHolidays/20\d{2}/[A-Z]{2})',url):raise ValueError('Unzulässiges API-Ziel')
        async with httpx.AsyncClient(timeout=6 if '/Holidays/' in url or '/PublicHolidays/' in url else 12,follow_redirects=False,trust_env=False) as client:
            async with client.stream('GET',url,params=params,headers={'User-Agent':'AIButler/1.0 (+https://github.com/Schello805/aibutler)','Accept':'application/json'}) as response:
                response.raise_for_status();body=bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body)>1024*1024:raise ValueError('API-Antwort zu groß')
                return json.loads(body)

    async def cached(self,kind,identity,ttl,fetch):
        key=kind+':'+hashlib.sha256(identity.encode()).hexdigest()
        async with self.locks[kind]:
            previous=self.store.public_cache(key)
            if previous and time.time()-previous['checked']<ttl:return {**previous['value'],'stale':False,'checked':previous['checked']}
            if self.failures.get(key,0)>time.monotonic():
                if previous:return {**previous['value'],'stale':True,'checked':previous['checked']}
                raise ValueError('Anbieter vorübergehend nicht verfügbar')
            try:
                budget=self.requests[kind];now=time.monotonic()
                while budget and now-budget[0]>=60:budget.popleft()
                if len(budget)>=(30 if kind=='weather' else 10):raise ValueError('Zu viele Datenabfragen; bitte kurz warten')
                budget.append(now)
                value=await fetch()
                self.store.save_public_cache(key,value)
                return {**value,'stale':False,'checked':time.time()}
            except Exception:
                self.failures[key]=time.monotonic()+60
                if len(self.failures)>200:self.failures={k:v for k,v in self.failures.items() if v>time.monotonic()}
                if previous:return {**previous['value'],'stale':True,'checked':previous['checked']}
                raise

    async def locations(self,query):
        city,separator,region_hint=query.partition(',');city=city.strip();region_hint=region_hint.strip().casefold()
        async def fetch():
            raw=await self.get_json('https://geocoding-api.open-meteo.com/v1/search',{'name':city,'count':5,'language':'de','format':'json'})
            german={'Freistaat Bayern':'DE-BY','Bayern':'DE-BY','Bavaria':'DE-BY','Baden-Württemberg':'DE-BW','Berlin':'DE-BE','Brandenburg':'DE-BB','Bremen':'DE-HB','Hamburg':'DE-HH','Hessen':'DE-HE','Hesse':'DE-HE','Mecklenburg-Vorpommern':'DE-MV','Niedersachsen':'DE-NI','Lower Saxony':'DE-NI','Nordrhein-Westfalen':'DE-NW','Rheinland-Pfalz':'DE-RP','Saarland':'DE-SL','Sachsen':'DE-SN','Sachsen-Anhalt':'DE-ST','Schleswig-Holstein':'DE-SH','Thüringen':'DE-TH'}
            results=[]
            for row in raw.get('results',[])[:5]:
                country=row['country_code'].upper();region=row.get('admin1','')
                if region_hint and region_hint not in (region+' '+country+' '+row.get('country','')).casefold():continue
                results.append(DeviceLocation(latitude=row['latitude'],longitude=row['longitude'],country=country,subdivision=german.get(region,'') if country=='DE' else '',label=', '.join(filter(None,[row['name'],region,country]))[:100],timezone=row.get('timezone','Europe/Berlin')).model_dump())
            return {'locations':results}
        value=await self.cached('geocoding',query.casefold(),30*86400,fetch)
        return value['locations']

    async def weather(self,location,day):
        async def fetch():
            raw=await self.get_json('https://api.open-meteo.com/v1/forecast',{'latitude':location.latitude,'longitude':location.longitude,'timezone':location.timezone,'forecast_days':7,'hourly':'temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m'})
            hourly=raw['hourly'];times=hourly['time']
            if not isinstance(times,list) or len(times)>200:raise ValueError('Ungültige Wetterdaten')
            rows=[]
            for index,stamp in enumerate(times):
                parsed=datetime.fromisoformat(stamp)
                if 7<=parsed.hour<=19:
                    row={'time':stamp}
                    for key in ('temperature_2m','precipitation_probability','precipitation','wind_speed_10m','wind_gusts_10m'):
                        value=hourly[key][index]
                        if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value)):raise ValueError('Ungültiger Wetterwert')
                        row[key]=value
                    rows.append(row)
            return {'hours':rows,'timezone':raw.get('timezone',''),'units':raw.get('hourly_units',{})}
        value=await self.cached('weather',f'{location.latitude},{location.longitude}:{location.timezone}',900,fetch)
        return {**value,'hours':[row for row in value['hours'] if row['time'].startswith(day.isoformat())],'date':day.isoformat()}

    async def holidays(self,location,day):
        if not location.country:raise ValueError('Land konnte nicht bestimmt werden')
        async def fetch():
            try:
                raw=await self.get_json(f'https://nagerholidays.com/api/v4/Holidays/{location.country}/{day.year}')
            except (httpx.HTTPError, OSError):
                raw=await self.get_json(f'https://date.nager.at/api/v3/PublicHolidays/{day.year}/{location.country}')
            if not isinstance(raw,list) or len(raw)>500:raise ValueError('Ungültige Feiertagsdaten')
            clean=[]
            for row in raw:
                date.fromisoformat(row['date'])
                subdivisions=row.get('subdivisionCodes',row.get('counties')) or []
                if not isinstance(subdivisions,list) or not all(isinstance(code,str) for code in subdivisions):raise ValueError('Ungültige Regionen')
                clean.append({'date':row['date'],'name':str(row.get('localName') or row['name'])[:200],'national':bool(row.get('nationalHoliday',row.get('global'))),'subdivisions':subdivisions,'public':'Public' in row.get('holidayTypes',row.get('types',[]))})
            return {'holidays':clean}
        value=await self.cached('holidays',f'{location.country}:{day.year}',86400,fetch)
        matches=[row for row in value['holidays'] if row['date']==day.isoformat() and row['public'] and (row['national'] or (location.subdivision and location.subdivision in row['subdivisions']))]
        return {'date':day.isoformat(),'country':location.country,'subdivision':location.subdivision,'holidays':matches,'regional_complete':bool(location.subdivision),'municipal_complete':False,'checked':value['checked'],'stale':value['stale']}

    async def context(self,question,location=None,history=()):
        want_weather,want_holidays=needs_public_context(question,history)
        result={'sources':[],'diagnostics':[]}
        requested=[name for name,wanted in [('weather',want_weather),('holidays',want_holidays)] if wanted]
        if not requested:return result
        active=[name for name in requested if getattr(self.settings,name+'_enabled')]
        if not active:
            result['diagnostics'].append('Wetter-/Feiertagsverbindungen sind noch nicht aktiviert.')
            return result
        if target_day(question,datetime.now(ZoneInfo(location.timezone if location else 'Europe/Berlin')).date()) is None:
            result['date_needed']=True
            result['diagnostics'].append('Der angefragte Tag ist mehrdeutig oder ungültig.')
            return result
        if location is None:
            result['location_needed']=True
            result['diagnostics'].append('Kein Gerätestandort übergeben. Standort freigeben oder einen Ort mit Koordinaten wählen.')
            return result
        today=datetime.now(ZoneInfo(location.timezone)).date();day=target_day(question,today)
        result['location']={'label':location.label,'country':location.country,'subdivision':location.subdivision,'note':'Standort des fragenden Geräts, nicht automatisch Ort des Vorhabens.'}
        for name in requested:
            if not getattr(self.settings,name+'_enabled'):
                result['diagnostics'].append(('Wetter' if name=='weather' else 'Feiertage')+' ist noch nicht aktiviert.')
                continue
            try:
                data=await (self.weather(location,day) if name=='weather' else self.holidays(location,day))
                result[name]=data
                if name=='holidays' and not data['regional_complete']:result['diagnostics'].append('Bundesland/Region unbekannt: nur landesweite Feiertage geprüft; regionale und kommunale Feiertage können fehlen.')
                title='Wetter: '+(location.label or 'Gerätestandort') if name=='weather' else 'Feiertage: '+location.country+(' / '+location.subdivision if location.subdivision else '')
                if name=='weather':
                    excerpt='Open-Meteo · Prognose für '+day.isoformat()+' · abgerufen '+datetime.fromtimestamp(data['checked'],ZoneInfo('UTC')).isoformat()+'\n'+'\n'.join(row['time'][11:16]+': '+str(row['temperature_2m'])+' °C, Regenwahrscheinlichkeit '+str(row['precipitation_probability'])+' %, Niederschlag '+str(row['precipitation'])+' mm, Wind '+str(row['wind_speed_10m'])+' km/h, Böen '+str(row['wind_gusts_10m'])+' km/h' for row in data['hours'])
                else:
                    excerpt='Nager.Date · '+day.isoformat()+' · '+location.country+' '+location.subdivision+'\n'+(', '.join(row['name'] for row in data['holidays']) or 'Kein passender öffentlicher Feiertag eingetragen.')+' Kommunale Sonderfälle sind nicht vollständig abgedeckt.'
                if data['stale']:excerpt+='\nVERALTETER CACHE-STAND – Anbieter aktuell nicht erreichbar.'
                result['sources'].append({'id':'public:'+name+':'+day.isoformat(),'source':name,'title':title,'excerpt':excerpt,'document_date':day.isoformat(),'url':'https://open-meteo.com/' if name=='weather' else 'https://nagerholidays.com/'})
                if data['stale']:result['diagnostics'].append(title+': Anbieter nicht erreichbar, veralteter Cache-Stand. Keine aktuelle Prognose garantiert.')
            except Exception:
                result['diagnostics'].append(('Wetter' if name=='weather' else 'Feiertage')+' konnte nicht abgerufen werden. Keine verlässliche Aussage aus dieser Quelle möglich.')
        return result
