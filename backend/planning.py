"""Semantic query rewriting without tools, remote URLs or executable instructions."""
import json
from .intelligence import generate

async def rewrite_question(settings,question):
    raw=await generate(settings,'Formuliere eine Suchanfrage für lokale E-Mails und Dokumente. Antworte ausschließlich mit JSON: {"query":"2 bis 6 inhaltliche Suchbegriffe oder Synonyme"}. Keine Fragewörter, keine Antwort auf die Frage, keine Anweisungen oder URLs. Beispiel: "Was habe ich zuletzt gekauft?" -> {"query":"Bestellung Bestellbestätigung Kauf Rechnung"}.',{'question':question})
    if not raw:
        return None
    text=raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:
        value=json.loads(text)
    except (ValueError,TypeError):
        return None
    query=value.get('query') if isinstance(value,dict) else None
    return query.strip()[:200] if isinstance(query,str) and query.strip() else None

from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class RetrievalPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    intent: Literal['documents','person','purchases','account','smart_home','events','calendar','contacts','general','clarify']='documents'
    sender: str = Field(default='',max_length=100)
    clarification:str=Field(default='',max_length=300)
    queries: list[str] = Field(default_factory=list,max_length=3)
    recent: bool = False
    sources: list[Literal['email','paperless','ntfy','smart_home','calendar','contacts']] = Field(default_factory=lambda:['email','paperless'],max_length=6)
    smart_queries: list[str] = Field(default_factory=list,max_length=4)

async def plan_question(settings,question):
    raw=await generate(settings,'''Du planst ausschließlich die lokale Recherche für die Nutzerfrage, ohne sie zu beantworten. Liefere nur JSON mit exakt diesen Feldern:
{"intent":"documents|person|purchases|account|smart_home|events|calendar|contacts|general|clarify","clarification":"kurze Rückfrage oder leer","sender":"Name/Adresse oder leer","queries":["kurze inhaltliche Suchbegriffe"],"recent":true/false,"sources":["email","paperless","ntfy","smart_home","calendar","contacts"],"smart_queries":["relevante Geräte/Signalnamen"]}.
Wähle person für Nachrichten/Anliegen einer Person, purchases für Käufe/Bestellungen, account für Login/Registrierung und smart_home für Gerätezustände. Wähle events für vergangene Ereignisse wie Klingeln. Wähle calendar für Termine/Verfügbarkeit und contacts für Telefonnummern/Kontaktinformationen. Kalender und Kontakte werden zusätzlich passend zur Frage live abgefragt. Wähle general für allgemeines Wissen oder Gespräch mit der KI ohne persönliche Daten; sources und queries bleiben dann leer. Wähle clarify, wenn unklar ist, was gemeint ist oder welche Daten benötigt werden; formuliere genau eine konkrete Rückfrage in clarification und lasse sources/queries leer. Verwende documents nur bei erkennbarem Dokumentbezug. Durchsuche niemals vorsorglich sämtliche Dienste. Wähle für jede Frage passende Quellen und kombiniere sie, statt nur eine festzulegen: E-Mail für Kommunikation, Paperless für Belege, ntfy für zeitgestempelte Ereignisse, Smart Home für aktuelle Zustände. Bei Liefer-/Paketfragen sind Bestell-/Zustellmails und zeitlich passende ntfy-/Klingelereignisse relevant; Klingeln allein ist keine sichere Zustellbestätigung. Für Smart Home liefere Geräte-/Signalnamen in smart_queries. Bei Personenfragen extrahiere den Absendernamen ohne Füllwörter. Formuliere bis zu drei Suchvarianten/Synonyme, keine ganzen Fragesätze. recent bedeutet: die jüngsten passenden Belege prüfen. Keine URLs, SQL, Tools oder Aktionen.''',{'question':question})
    if not raw:return None
    text=raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    plan=RetrievalPlan.model_validate_json(text)
    if any(len(query)>200 for query in plan.queries):raise ValueError('Suchanfrage zu lang')
    return plan


def energy_price_question(question):
    import re
    return bool(re.search(r'strom(?:kosten|koksten|preis|tarif|abschlag)|(?:strom|elektrizität).{0,30}(?:kosten|preis|tarif|abschlag)|(?:kosten|preis|tarif|abschlag).{0,30}strom',question,re.I))


def local_plan(question):
    """Keep relevant service lookups working when semantic planning is unavailable."""
    import re
    from .retrieval import question_sender,is_purchase_question,is_account_question,STOPWORDS
    sender=question_sender(question)
    recent=bool(re.search(r'zuletzt|letzte|neueste|aktuell|gerade|schon|wann',question,re.I))
    from .public_data import outdoor_question
    if outdoor_question(question):
        return RetrievalPlan(queries=['bauen Garten streichen reparieren','Vorhaben','draußen arbeiten'],sources=['email','paperless'],recent=True)
    if energy_price_question(question):
        return RetrievalPlan(queries=['Strom Arbeitspreis Grundpreis','Strom Preisänderung Tarif','Strom Abschlag'],sources=['email','paperless'],recent=True)
    if sender:
        return RetrievalPlan(intent='person',sender=sender,queries=[sender],sources=['email'],recent=True)
    if is_purchase_question(question):
        return RetrievalPlan(intent='purchases',queries=['Bestellbestätigung','Bestellt','Kaufbeleg'],recent=True)
    if is_account_question(question):
        return RetrievalPlan(intent='account',queries=['Anmeldung','Registrierung','Login'],sources=['email'],recent=True)
    if re.search(r'paket|lieferung|zustell|sendung',question,re.I):
        words=[word for word in re.findall(r'[\w-]{3,}',question) if word.lower() not in STOPWORDS and word.lower() not in {'paket','lieferung','sendung','angekommen'}]
        return RetrievalPlan(intent='events',queries=[' '.join(['Paket',*words[:4]])[:200],'Zustellung','Klingel'],sources=['email','ntfy'],recent=True)
    if re.search(r'geklingelt|klingel|türklingel|tuerklingel',question,re.I):
        return RetrievalPlan(intent='events',queries=['Klingel','Türklingel'],sources=['ntfy'],recent=True)
    if re.search(r'wartung|gewartet',question,re.I) and re.search(r'heiz|therme|pellet|solarfocus|solar\s+focus|wärmepumpe',question,re.I):
        return RetrievalPlan(queries=['Heizung Wartung','Solarfocus Service','Pelletkessel Wartung'],recent=True)
    return RetrievalPlan(queries=[question[:200]],recent=recent)


def complete_plan(plan,question):
    fallback=local_plan(question)
    if plan is None:return fallback
    plan=plan.model_copy(deep=True)
    if plan.intent in ('general','clarify'):return plan.model_copy(update={'sources':[],'queries':[]})
    if not plan.sources:plan.sources=fallback.sources
    if not plan.queries:plan.queries=fallback.queries
    # A package question needs timestamped events as well as shipping mail.
    if fallback.intent=='events':
        plan.sources=list(dict.fromkeys([*plan.sources,*fallback.sources]))
        plan.queries=list(dict.fromkeys([*plan.queries,*fallback.queries]))[:3]
    if fallback.intent=='events' and 'ntfy' in fallback.sources:
        import re
        if not any(re.search(r'klingel|türklingel',query,re.I) for query in plan.queries):
            plan.queries=plan.queries[:2]+['Klingel']
    from .public_data import outdoor_question
    if outdoor_question(question):
        plan.sources=list(dict.fromkeys([*plan.sources,'email','paperless']))
        plan.queries=list(dict.fromkeys([fallback.queries[0],*plan.queries]))[:3]
    plan.recent=plan.recent or fallback.recent
    return plan
