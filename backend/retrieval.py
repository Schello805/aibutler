"""Conservative local question retrieval and short, evidence-aware responses."""
import re

STOPWORDS=set('''aber als also auch auf aus bei bis bitte da dabei das dass dein deine dem den der des dich die dies diese dieser dieses doch du durch ein eine einem einen einer eines er es etwas für gegen gerade habe haben hat hatte hier ich im in ist kann kannst letzte letzten letzter letztes mehr meine meinen mich mir mit nach noch nun oder ohne schon sehr sich sie sind so über um und uns unser unter vom von vor war waren was wegen welche welcher welches wem wen wenn wer wie wieder wird wir wo woran wurde zum zur wann zeig zeigen findest finden suche such bitte denn mal aktuell aktuelle aktuellen meinem meiner'''.split())

def is_account_question(question):
    return bool(re.search(r'angemeldet|eingeloggt|registriert|letzte.{0,15}(?:login|anmeldung|registrierung)|neues.{0,10}konto',question,re.I))


def message_text(body):
    if not re.search(r'<(?:html|body|div|p|table)\b',body,re.I):
        return body
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self):super().__init__();self.hidden=0;self.parts=[]
        def handle_starttag(self,tag,attrs):
            if tag in ('head','style','script'):self.hidden+=1
            elif not self.hidden and tag in ('p','div','tr','br','li'):self.parts.append('\n')
        def handle_endtag(self,tag):
            if tag in ('head','style','script') and self.hidden:self.hidden-=1
            elif not self.hidden and tag in ('p','div','tr','li'):self.parts.append('\n')
        def handle_data(self,text):
            if not self.hidden:self.parts.append(text)
    parser=Text();parser.feed(body)
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def account_activity_kind(hit):
    title=hit['title'];text=message_text(hit['body'])
    if re.search(r'login.?code|temporary.{0,15}code|passwor.{0,25}(?:reset|zurück)|verifizier|verification',title,re.I):
        return None  # A code/request does not prove a completed sign-in or account creation.
    if re.search(r'new sign.in|neuer? login|admin login|neue anmeldung|successful.{0,10}(?:login|sign.in)|anmeldung erfolgreich',title,re.I):
        return 'login'
    if re.search(r'willkommen|welcome|registrierung|account created|konto erstellt',title,re.I):
        if re.search(r'neuen kunden|kundenlogin|konto.{0,40}(?:erstellt|aktiviert)|account.{0,40}(?:created|registered)|registrierung.{0,40}(?:erfolgreich|abgeschlossen)',text,re.I|re.S) and not re.search(r'domain.{0,30}registriert',title,re.I):
            return 'signup'
    return None


def account_provider(hit):
    title=hit['title']
    for pattern in [r'(?i)sign.in to your (.+?) account',r'(?i)(?:login bei|willkommen bei|welcome to|anmeldung bei)\s+([^:–]+)',r'(?i)\]\s*(\S+)\s+Admin Login']:
        match=re.search(pattern,title)
        if match:return match[1].strip()
    name=hit.get('sender_name','').strip()
    return name or hit.get('sender_address','').split('@')[-1] or title


def account_answer(question,hits):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    kind='signup' if re.search(r'registrier|neues.{0,10}konto',question,re.I) else 'login' if re.search(r'eingeloggt|login',question,re.I) else None
    def describe(hit,registration=False):
        date=hit.get('document_date') or ''
        when=''
        if date:
            try:
                day=datetime.fromisoformat(date.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Berlin')).date()
                when='heute' if day==datetime.now(ZoneInfo('Europe/Berlin')).date() else 'vom '+day.strftime('%d.%m.%Y')
            except ValueError:pass
        provider=account_provider(hit)
        return ('Die letzte erkennbare Neuregistrierung war bei '+provider if registration else 'Die neueste Anmeldebestätigung stammt von '+provider)+((' ('+when+')') if when else '')+'.'
    if kind:
        match=next((hit for hit in hits if hit.get('activity_kind')==kind),None)
        return describe(match,kind=='signup') if match else 'Dazu finde ich gerade keine bestätigte Anmeldung in deinen E-Mails.'
    login=next((h for h in hits if h.get('activity_kind')=='login'),None)
    signup=next((h for h in hits if h.get('activity_kind')=='signup'),None)
    answer=describe(login) if login else 'Ich finde gerade keine bestätigte Konto-Anmeldung.'
    if signup:answer+=' Falls du eine neue Registrierung meinst: '+account_provider(signup)+(' am '+signup['document_date'][:10].split('T')[0].split('-')[2]+'.'+signup['document_date'][:10].split('-')[1]+'.'+signup['document_date'][:4] if signup.get('document_date') else '')+'.'
    return answer


def is_purchase_question(question):
    return bool(re.search(r'gekauft|bestellt|letzte[nrs]?\s+(?:kauf|bestellung|einkauf)|neueste[nrs]?\s+(?:kauf|bestellung)',question,re.I))


def purchase_evidence(hit):
    title=hit['title']
    if re.search(r'newsletter|rabatt|storniert|stornierung|retoure|versandt|versendet|versandbestätigung',title,re.I):return False
    return bool(re.search(r'bestellt|bestellbestätigung|bestellung bestätigt|order confirmation|kaufbeleg|kassenbon|quittung',title,re.I) or re.search(r'bestellnummer|vielen dank für (?:deine|ihre) bestellung',message_text(hit['body']),re.I))


def purchase_answer(hits):
    if not hits:
        return 'Ich finde gerade keinen passenden Kaufbeleg.'
    hit=hits[0];body=message_text(hit['body'])
    products=re.findall(r'(?m)^\s*\*\s+([^\n]+)',body)
    if products:
        names=[]
        for product in products[:3]:
            # Product descriptions can be lengthy; use their identifying first clause.
            name=product.split(',')[0].strip()
            names.append(name[:100])
        items=', '.join(names[:-1])+' und '+names[-1] if len(names)>1 else names[0]
    else:
        items=re.sub(r'(?i)^(?:bestellt|bestellbestätigung|ihre bestellung)\s*:?\s*','',hit['title']).strip(' „“')
    total=re.search(r'(?i)(?:summe|gesamtbetrag|gesamt)[\s:]*([\d.,]+)\s*(?:EUR|€)',body)
    amount=total[1] if total else ''
    if amount and ',' not in amount and re.search(r'\.\d{2}$',amount):amount=amount[:-3]+','+amount[-2:]
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+\.\d{2}',amount):amount=amount.replace(',','').replace('.',',')
    cost=(' für insgesamt '+amount+' €') if total else ''
    return 'Zuletzt hast du '+items+cost+' bestellt.'


def question_sender(question):
    name=r'([A-Za-zÄÖÜäöüß][\w.@+-]*(?:\s+[A-ZÄÖÜ][\w.-]*)?)'
    patterns=[r'(?i:\b(?:wollte|will|möchte|brauchte|braucht|schrieb|fragte|fragt))\s+'+name,
              r'(?i:\b(?:mail|mails|nachricht|nachrichten|e-mail|e-mails)\s+von)\s+'+name]
    for pattern in patterns:
        match=re.search(pattern,question)
        if match and match[1].lower() not in {'ich','du','er','sie','es','man','mir','dir','wissen','gerne','gern'}:
            return match[1]
    return None


def sender_answer(name,hits):
    if not hits:
        return f'Ich finde gerade keine Nachricht von {name} im eingerichteten Postfach.'
    body=message_text(hits[0]['body'])
    # Avoid treating quoted earlier correspondence as the sender's newest request.
    body=re.split(r'(?im)^\s*(?:Am .{8,100}schrieb|On .{8,100}wrote|Von:|From:|-----Original|>{1,})',body)[0]
    lines=[line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith(('http://','https://'))]
    text=' '.join(lines)
    text=re.sub(r'(?i)^(?:hallo|hi|lieber|guten tag)\s+[^,!.]{1,35}[,!.]\s*','',text)
    request=re.search(r'(?i)(?:kannst du|könntest du|bitte|ich brauche|ich benötige|ich hätte gerne)[^.!?]{8,300}[.!?]?',text)
    if request:
        # Include the object/reason preceding "bitte", rather than returning a bare command.
        start=max(0,request.start()-240)
        context=text[start:request.start()]
        boundaries=list(re.finditer(r'[.!?]\s+',context))
        if boundaries:
            chosen=boundaries[-1]
            if re.match(r'(?i)(?:das|dies|es|den|die)\b',context[chosen.end():].strip()):
                chosen=boundaries[-2] if len(boundaries)>1 else None
            start+=chosen.end() if chosen else 0
        elif start:
            boundary=text.find(' ',start)
            start=boundary+1 if boundary>=0 else start
        excerpt=text[start:min(len(text),request.end())].strip()
    else:
        excerpt=text[:340].strip()
    if not excerpt:
        return f'Die letzte Nachricht von {name} hat den Betreff „{hits[0]["title"]}“.'
    return f'{name} schrieb zuletzt: „{excerpt}“'


def query_terms(query):
    words=list(dict.fromkeys(w for w in re.findall(r'\w{3,}',query.lower()) if w not in STOPWORDS))[:24]
    expressions=[]
    for word in words:
        if word.startswith(('heizung','heizungs')):
            expressions.append('("heiz"* OR "solarfocus"* OR "pellet"* OR "therme"* OR "wärmepumpe"*)')
            if 'wartung' in word:
                expressions.append('("wart"* OR "service"* OR "heizungswartung"*)')
        elif word.startswith('wartung'):
            expressions.append('("wart"* OR "service"* OR "heizungswartung"*)')
        elif word.startswith('rechnung'):
            expressions.append('"rechnung"*')
        else:
            expressions.append('"'+word+'"')
    return list(dict.fromkeys(expressions))

def local_answer(question, hits, unavailable):
    if is_account_question(question):
        return account_answer(question,hits)
    if is_purchase_question(question):
        return purchase_answer(hits)
    sender=question_sender(question)
    if sender:
        return sender_answer(sender,hits)
    dated=maintenance_answer(question,hits)
    if dated:
        return dated
    if re.search(r'wartung|gewartet',question,re.I):
        return 'Ein eindeutiges Wartungsdatum finde ich in deinen Unterlagen gerade nicht.'
    if not hits:
        return 'Dazu finde ich in deinen Unterlagen gerade keinen passenden Beleg.'
    return 'Ich habe passende Unterlagen gefunden. Für eine inhaltliche Antwort kannst du die KI-Auswertung einschalten.'


def evidence_excerpt(body,query,matched=''):
    text=message_text(body)
    if len(text)<=10000:return text
    position=text.find(message_text(matched).strip()[:100]) if matched else 0
    if re.search(r'wartung|service|gewartet',query,re.I):
        execution=re.search(r'leistungsdatum|ausführungstermin|ausgeführt|durchgeführt|wartungsarbeiten',text,re.I)
        if execution:position=execution.start()
    if position<2000:return text[:10000]
    start=max(2000,position-2000)
    return text[:2000]+'\n[… weiterer Dokumentauszug …]\n'+text[start:start+7900]


def license_question(question):
    return bool(re.search(r'führerschein|fuehrerschein|fahrerlaubnis',question,re.I))


def relevant_evidence(question,title,body):
    if license_question(question):
        if re.search(r'newsletter|ratgeber|reform|versicherung|angebot|rechnung|jobs?|handbuch|anleitung',title,re.I):return False
        return bool(re.search(r'führerschein|fuehrerschein|fahrerlaubnis|prüfungsbescheinigung',title,re.I))

    if re.search(r'paket|lieferung|zustell|geklingelt|klingel',question,re.I) and re.search(r'newsletter|rabatt|sonderangebot|stellenangebot',title,re.I):
        return False
    if not re.search(r'wartung|gewartet',question,re.I):
        return True
    if re.search(r'newsletter|\bjobs?\b|stellenangebot|sucht.{0,50}(meister|techniker|consultant)|karriere',title,re.I):
        return False
    if re.search(r'heiz|therme|pellet|wärmepumpe|solarfocus|solar\s+focus',question,re.I):
        text=title+'\n'+body
        if not re.search(r'heiz|therme|pellet|wärmepumpe|solarfocus|solar\s+focus',text,re.I):
            return False
        return bool(re.search(r'heiz|therme|pellet|wärmepumpe|solarfocus|solar\s+focus',title,re.I) or re.search(r'termin|rechnung|service|wartung|bestätigung|auftrag',title,re.I) or re.search(r'(heiz\w*|therme|pellet\w*|wärmepumpe|solarfocus).{0,200}(wart\w*|service)|(wart\w*|service).{0,200}(heiz\w*|therme|pellet\w*|wärmepumpe|solarfocus)',text,re.I|re.S))
    return True


def maintenance_facts(hit):
    from datetime import datetime
    body=hit['body'];title=hit['title'];facts=[]
    if not re.search(r'wartung|gewartet',body+' '+title,re.I):
        return facts
    completed=bool(re.search(r'geleistet|durchgeführt|ausgeführt|wartungsarbeiten\s+vom|wartungsprotokoll|servicebericht',body+' '+title,re.I))
    cancelled=bool(re.search(r'stornierung|storniert|abgesagt',title,re.I))
    if cancelled:return facts
    if completed:
        pattern=r'(?:wartungscheckliste|wartungsarbeiten|wartung|gewartet|leistungsdatum|ausführungstermin|ausgeführt|durchgeführt)[^\d\n]{0,55}(\d{1,2})[./](\d{1,2})[./](\d{4})'
        for match in re.finditer(pattern,body,re.I):
            context=body[max(0,match.start()-30):match.end()+40]
            if re.search(r'nicht.{0,20}(?:durchgeführt|ausgeführt)|geplant|voraussichtlich|vorgesehen',context,re.I):continue
            try:
                day=datetime(int(match[3]),int(match[2]),int(match[1]))
                if day.date()<=datetime.now().date():
                    facts.append((day,'exact',f'Die letzte belegte Heizungswartung war am {day:%d.%m.%Y}.'))
            except ValueError:
                pass
        for match in re.finditer(r'(?:ausführungstermin|leistungszeitraum|wartung)[^\n]{0,40}?KW\s*(\d{1,2})\s*[/.-]\s*(\d{4})',body,re.I):
            try:
                start=datetime.fromisocalendar(int(match[2]),int(match[1]),1)
                end=datetime.fromisocalendar(int(match[2]),int(match[1]),7)
                if start.date()<=datetime.now().date():
                    facts.append((start,'week',f'Die letzte belegte Heizungswartung war in KW {int(match[1])}/{match[2]}, also vom {start:%d.%m.} bis {end:%d.%m.%Y}.'))
            except ValueError:
                pass
    # A recent maintenance invoice is useful evidence even without an explicit execution day.
    # Its date supports a likely period, not an invented exact maintenance date.
    if not facts and re.search(r'rechnung|invoice',title+' '+body[:500],re.I) and not re.search(r'angebot|geplant|terminbestätigung',title,re.I):
        raw=hit.get('document_date','')
        try:
            day=datetime.fromisoformat(raw[:10])
            if day.date()<=datetime.now().date():
                month=['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'][day.month-1]
                facts.append((day,'inferred',f'Die letzte Heizungswartung war wahrscheinlich im {month} {day.year} – darauf deutet die neueste Wartungsrechnung hin.'))
        except (ValueError,TypeError):
            years=re.findall(r'\b(20\d{2})\b',title)
            if years and int(max(years))<=datetime.now().year:
                year=int(max(years));facts.append((datetime(year,1,1),'inferred',f'Die letzte Heizungswartung war wahrscheinlich {year} – darauf deutet die neueste Wartungsrechnung hin.'))
    return facts


def maintenance_answer(question,hits):
    if not re.search(r'wartung|gewartet',question,re.I):
        return None
    facts=[fact for hit in hits for fact in maintenance_facts(hit)]
    return max(facts,key=lambda f:f[0])[2] if facts else None


def evidence_recency(hit):
    from datetime import datetime
    facts=maintenance_facts(hit)
    if facts:
        return max(f[0] for f in facts).isoformat()
    date=hit.get('document_date','')
    try:
        return datetime.fromisoformat(date[:10]).isoformat()
    except (ValueError,TypeError):
        return ''
