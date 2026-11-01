"""Cheap, conservative conversation routing before any provider or source request."""
import re
from .planning import RetrievalPlan,energy_price_question


def route_question(question,last=None):
    last=last or {};text=question.strip();lower=text.casefold()
    if len(text.split())<=25 and re.search(r'(?:kannst du|verstehst du|hörst du|hoerst du).{0,35}mich.{0,35}(?:versteh|hören|hoeren)|(?:verstehst|hörst|hoerst) du mich',lower):
        return {'kind':'reply','answer':'Deine Nachricht ist angekommen und verständlich. Die Tonqualität selbst kann ich anhand des erkannten Textes nicht beurteilen.'}
    if re.fullmatch(r'(?:hallo|hi|hey|servus|moin|guten morgen|guten abend)[\s!.]*(?:butler)?[\s!.]*',lower):
        return {'kind':'reply','answer':'Hallo! Was möchtest du wissen?'}
    if re.fullmatch(r'(?:was kannst du(?: alles)?|(?:wobei|wie) kannst du mir helfen)[\s?!.]*',lower):
        return {'kind':'reply','answer':'Ich kann Fragen zu deinen verbundenen E-Mails, Dokumenten, Kalendern und Smart-Home-Daten beantworten. Wenn eine Frage unklar ist, frage ich nach.'}
    if last.get('device_question') and last.get('clarification')=='electricity_costs' and len(text.split())<=8:
        text=last['device_question']+' '+text;lower=text.casefold()
    energy=energy_price_question(text)
    followup=last.get('clarification')=='electricity_costs'
    if followup and not energy and re.match(r'^(?:was|wie|wann|warum|wer|wo|kannst)\b',lower) and not re.search(r'abschlag|tarif|kosten|strom|gerät|geraet|woche|monat|jahr|verbrauch',lower):followup=False
    if energy or followup:
        scope=None
        if re.search(r'abschlag|monatlich(?:er|en|e)? zahl|vorauszahlung',lower):scope='advance'
        elif re.search(r'arbeitspreis|grundpreis|tarif|kilowattstunde|kwh.?preis',lower):scope='tariff'
        elif re.search(r'gerät|geraet|kühlschrank|kuehlschrank|waschmaschine|wärmepumpe|waermepumpe',lower):scope='device'
        elif re.search(r'jahr|woche|monat|gestern|heute|verbrauch|entstanden|tatsächlich|tatsaechlich',lower):scope='usage'
        if not scope:
            # Do not trap an unrelated new question in a previous clarification.
            if followup and not energy and len(text.split())>5:return None
            return {'kind':'clarify','answer':'Meinst du deinen monatlichen Abschlag, den Tarif pro Kilowattstunde oder die tatsächlich entstandenen Kosten für einen Zeitraum beziehungsweise ein bestimmtes Gerät?','clarification':'electricity_costs'}
        enriched=text if energy else 'Stromkosten: '+text
        if scope=='device' and re.search(r'(?:ein(?:em|en)?|bestimmt(?:es|en|em)?) gerät|^gerät[.!?]*$',lower):
            return {'kind':'clarify','answer':'Welches Gerät und welchen Zeitraum meinst du?','clarification':'electricity_costs'}
        if scope=='device' and not re.search(r'heute|gestern|woche|monat|jahr|tag|20\d{2}|seit|zeitraum',lower):
            return {'kind':'clarify','answer':'Für welchen Zeitraum möchtest du die Stromkosten dieses Geräts wissen?','clarification':'electricity_costs','device_question':enriched}
        sources=['smart_home','paperless'] if scope=='device' else ['paperless','smart_home'] if scope=='usage' else ['email','paperless']
        queries=['Strom Abschlag Vorauszahlung'] if scope=='advance' else ['Strom Arbeitspreis Grundpreis','Strom Preisänderung Tarif'] if scope=='tariff' else [enriched[:200],'Strom Arbeitspreis Tarif']
        return {'kind':'sources','message':enriched,'plan':RetrievalPlan(intent='smart_home' if scope=='device' else 'documents',queries=queries,sources=sources,recent=True,smart_queries=[enriched[:200]] if 'smart_home' in sources else [])}
    advice=bool(re.search(r'sollte ich.{0,35}(?:denken|beachten|mitnehmen)|worauf soll(?:te)? ich achten|hast du.{0,15}tipps|was.{0,20}(?:vorbereiten|beachten)',lower))
    new_job=bool(re.search(r'neue[nmrs]?\s+(?:job|stelle|arbeit)|erster.{0,8}arbeitstag|arbeitsbeginn|jobstart',lower))
    if advice and new_job and not re.search(r'e-?mail|postfach|dokument|kalender|einladung|nachricht|prüfe|pruefe|schau nach',lower):
        return {'kind':'general','advice':True,'fallback':'Für deinen ersten Tag: Bestätige Startzeit, Ansprechpartner und Treffpunkt, plane die Anfahrt mit Puffer und lege die vereinbarten Unterlagen bereit. Frag nach, ob du besondere Kleidung oder Ausrüstung mitbringen sollst.'}
    from .retrieval import license_question
    if license_question(text):return {'kind':'sources','message':text,'plan':RetrievalPlan(queries=['Führerschein','Fahrerlaubnis'],sources=['paperless'])}
    sources=[]
    if re.search(r'e-?mail|postfach|nachricht|geschrieben',lower):sources.append('email')
    if re.search(r'paperless|dokument|rechnung|beleg',lower):sources.append('paperless')
    if re.search(r'iobroker|smart.?home|sensor|stromzähler|stromzaehler|gerät|geraet|offline|online|erreichbar|thermostat',lower):sources.append('smart_home')
    if re.search(r'ntfy|geklingelt|klingel',lower):sources.append('ntfy')
    from .icloud import wants_calendar,wants_contacts
    if wants_calendar(text):sources.append('calendar')
    if wants_contacts(text):sources.append('contacts')
    from .public_data import outdoor_question
    if outdoor_question(text):sources.extend(['email','paperless','calendar'])
    if sources:
        if last.get('clarification')=='source_scope' and len(text.split())<=6:text=last.get('pending_question','')+' '+text
        from .planning import local_plan
        plan=local_plan(text).model_copy(update={'sources':list(dict.fromkeys(sources))})
        return {'kind':'sources','message':text,'plan':plan}
    if re.match(r'^(?:was (?:ist|sind|bedeutet)|erkläre|erklaere|wie funktioniert|übersetze|uebersetze)\b',lower) and not re.search(r'\bmein\w*|\bunser\w*|e-?mail|dokument|rechnung|kalender|iobroker|smart.?home|stromkosten',lower):
        return {'kind':'general'}
    return None
