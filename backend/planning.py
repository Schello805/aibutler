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
    intent: Literal['documents','person','purchases','account','smart_home','events']='documents'
    sender: str = Field(default='',max_length=100)
    queries: list[str] = Field(default_factory=list,max_length=3)
    recent: bool = False
    sources: list[Literal['email','paperless','ntfy','smart_home']] = Field(default_factory=lambda:['email','paperless'],max_length=4)
    smart_queries: list[str] = Field(default_factory=list,max_length=4)

async def plan_question(settings,question):
    raw=await generate(settings,'''Du planst ausschließlich die lokale Recherche für die Nutzerfrage, ohne sie zu beantworten. Liefere nur JSON mit exakt diesen Feldern:
{"intent":"documents|person|purchases|account|smart_home|events","sender":"Name/Adresse oder leer","queries":["kurze inhaltliche Suchbegriffe"],"recent":true/false,"sources":["email","paperless","ntfy","smart_home"],"smart_queries":["relevante Geräte/Signalnamen"]}.
Wähle person für Nachrichten/Anliegen einer Person, purchases für Käufe/Bestellungen, account für Login/Registrierung und smart_home für Gerätezustände. Wähle events für vergangene Ereignisse wie Klingeln. Sonst documents. Wähle für jede Frage passende Quellen und kombiniere sie, statt nur eine festzulegen: E-Mail für Kommunikation, Paperless für Belege, ntfy für zeitgestempelte Ereignisse, Smart Home für aktuelle Zustände. Bei Liefer-/Paketfragen sind Bestell-/Zustellmails und zeitlich passende ntfy-/Klingelereignisse relevant; Klingeln allein ist keine sichere Zustellbestätigung. Für Smart Home liefere Geräte-/Signalnamen in smart_queries. Bei Personenfragen extrahiere den Absendernamen ohne Füllwörter. Formuliere bis zu drei Suchvarianten/Synonyme, keine ganzen Fragesätze. recent bedeutet: die jüngsten passenden Belege prüfen. Keine URLs, SQL, Tools oder Aktionen.''',{'question':question})
    if not raw:return None
    text=raw.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    plan=RetrievalPlan.model_validate_json(text)
    if any(len(query)>200 for query in plan.queries):raise ValueError('Suchanfrage zu lang')
    return plan
