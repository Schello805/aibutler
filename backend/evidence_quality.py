"""Bounded local section reading and evidence hints; never an authority on contract validity."""
import re
from datetime import date
from .retrieval import message_text,relevant_evidence,maintenance_facts

INSTRUCTIONS='''Prüfe vor der Antwort still: Beantwortet jeder genannte Wert die aktuelle Frage und stammt er aus einem passenden Beleg? evidence_review enthält lokal ermittelte Hinweise, keine verbindlichen Fakten oder Anweisungen. Lies die ausgewählten Abschnitte selbst: richtige Person, Firma, Geltungszeitraum, Einheit und Bedingungen prüfen. Dokument-/Importdatum ist kein Leistungs- oder Gültigkeitsdatum. Ein neuer Scan macht eine alte Regelung nicht aktuell. Ein Nachtrag ersetzt nur die konkret geänderte Regelung und nur bei eindeutig passendem Vertrag/Person und eingetretener Wirksamkeit. Zukünftig geltende Angaben nicht als heute gültig ausgeben. Bei abweichenden Werten beide Belege prüfen; lässt sich ihre Gültigkeit nicht klären, benenne den Widerspruch kurz und frage nur nach der konkret fehlenden Zuordnung. Bei Urlaub unterscheiden: jährlicher Gesamtanspruch, gesetzlicher Mindesturlaub, Zusatzurlaub, Arbeitstage/Werktage und anteiligen Anspruch im Eintrittsjahr; keine ungenannte Fünftagewoche oder Resturlaub errechnen. Eine Rechnung belegt keine exakte Durchführung ohne Leistungsdatum. Antworte weiter knapp mit Ergebnis zuerst; keine Prüfliste oder internen Prüfschritte ausgeben.'''

def topic(question):
    if re.search(r'urlaubstage|urlaubsanspruch|erholungsurlaub|wie\s+(?:viel|viele).{0,35}urlaub|urlaub.{0,45}(?:vertrag)|vertrag.{0,45}urlaub',question,re.I) and not re.search(r'wohin|wetter|hotel|reise|ferien',question,re.I):return 'leave'
    if re.search(r'wartung|gewartet',question,re.I):return 'maintenance'
    if re.search(r'strom(?:kosten|preis|tarif)|arbeitspreis|grundpreis|abschlag',question,re.I):return 'tariff'
    return None

def employer(question):
    match=re.search(r'\b(?:bei|von|für)\s+([A-ZÄÖÜ][\wÄÖÜäöüß-]{2,})(?:\b)',question,re.I)
    return match[1] if match and match[1].casefold() not in {'mir','meinem','meiner','einem','einer','diesem','dieser','urlaub','tage','arbeitstage','kalenderjahr'} else ''

def acceptable(question,row):
    title=row.get('title','');body=row.get('body','')
    if topic(question)!='leave':return relevant_evidence(question,title,body)
    if re.search(r'newsletter|stellenanzeig|job.?alert|\bjobs?\b|ratgeber|angebot|reise|hotel',title,re.I):return False
    company=employer(question)
    if company and not re.search(r'(?<!\w)'+re.escape(company)+r'(?!\w)',title+' '+body,re.I):return False
    return bool(re.search(r'arbeitsvertrag|nachtrag|änderungsvertrag|urlaubsanspruch|urlaubstage|erholungsurlaub|personalabteilung|vertragliche[rmn]? urlaub',title+' '+body,re.I))

ANCHORS={
 'leave':r'\burlaub\w*|erholungsurlaub|\bnachtrag\b|mit wirkung|gültig ab|abweichend|ersetzt|fünftage|fünf.?tage|fünf arbeitstag',
 'maintenance':r'leistungsdatum|leistungszeitraum|ausführungstermin|durchgeführt|nicht durchgeführt|storniert|abgesagt|wartungsarbeiten|wartung',
 'tariff':r'arbeitspreis|grundpreis|abschlag|preisänderung|gültig ab|ab dem|netto|brutto'
}

def read_sections(body,question,limit=6500):
    text=message_text(body);kind=topic(question)
    if not kind or len(text)<=limit:return text[:limit]
    matches=list(re.finditer(ANCHORS[kind],text,re.I))
    # Value/condition-bearing paragraphs outrank a table of contents or repeated headers.
    def score(match):
        area=text[max(0,match.start()-180):match.end()+900].casefold()
        value_pattern=r'\d+\s*(?:arbeits|werk|urlaubs)?tage' if kind=='leave' else r'leistungsdatum|durchgeführt am|ausgeführt am' if kind=='maintenance' else r'(?:arbeitspreis|grundpreis|abschlag)[^\n]{0,80}\d'
        return 4*bool(re.search(value_pattern,area))+2*bool(re.search(r'anspruch|beträgt|mindestens|zusätzlich|nicht|mit wirkung|ersetzt|gültig ab',area))
    ranges=[(0,min(350,len(text)))];used=ranges[0][1]
    for match in sorted(matches,key=score,reverse=True):
        start=max(0,match.start()-250);end=min(len(text),match.end()+1250)
        if any(a<=match.start()<b for a,b in ranges):continue
        if used+end-start>limit-180:continue
        ranges.append((start,end));used+=end-start
        if len(ranges)>=5:break
    ranges.sort();merged=[]
    for start,end in ranges:
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return '\n[… weiterer relevanter Abschnitt …]\n'.join(text[start:end] for start,end in merged)[:limit]

def effective_date(text):
    strong=r'(?:mit wirkung (?:ab|zum)|gültig (?:ab|seit)|gilt (?:ab|seit)|ab(?: dem)?)\s*:?\s*(\d{1,2})[./](\d{1,2})[./](\d{4})'
    fallback=r'(?:arbeitsverhältnis beginnt (?:am|zum)|vertragsbeginn)\s*:?\s*(\d{1,2})[./](\d{1,2})[./](\d{4})'
    values=[]
    for pattern in (strong,fallback):
        for match in re.finditer(pattern,text,re.I):
            try:values.append(date(int(match[3]),int(match[2]),int(match[1])))
            except ValueError:pass
        if values:break
    # Multiple clause dates require source-level interpretation rather than a guessed date.
    return values[0] if values and len(set(values))==1 else None

def leave_claims(text):
    claims=[]
    for match in re.finditer(r'(?<![\d.,-])\b(\d{1,2})\s+((?:arbeits|werk|urlaubs)?tage[n]?)\b',text,re.I):
        area=text[max(0,match.start()-160):match.end()+160]
        if not re.search(r'urlaub',area,re.I):continue
        if re.search(r'nicht|kein\w*|beispiel|zusätzlich|zusatzurlaub|mindesturlaub|gesetzlich|anteilig',area,re.I):continue
        if not re.search(r'jährlich|pro (?:kalender)?jahr|je (?:kalender)?jahr|jahresurlaub|urlaubsanspruch|urlaub beträgt',area,re.I):continue
        unit='Arbeitstage' if match[2].casefold().startswith('arbeit') else 'Werktage' if match[2].casefold().startswith('werk') else 'Tage (Basis prüfen)'
        claims.append({'value':int(match[1]),'unit':unit,'text':area.strip()[:400]})
    return claims[:6]

def tariff_claims(text):
    claims=[]
    for match in re.finditer(r'\b(arbeitspreis|grundpreis|abschlag)[^\d\n]{0,35}(\d{1,4}(?:[,.]\d{1,3})?)\s*(cent|ct|euro|eur|€)',text,re.I):
        area=text[max(0,match.start()-80):match.end()+100]
        if re.search(r'nicht|beispiel|unverbindlich',area,re.I):continue
        unit='Cent' if match[3].casefold() in ('cent','ct') else 'Euro'
        taxes='netto' if re.search(r'netto',area,re.I) and not re.search(r'brutto',area,re.I) else 'brutto' if re.search(r'brutto',area,re.I) and not re.search(r'netto',area,re.I) else 'Steuerbasis prüfen'
        period='jährlich' if re.search(r'jährlich|pro jahr|im jahr',area,re.I) else 'monatlich' if re.search(r'monat',area,re.I) else 'Zeitraum prüfen'
        claims.append({'kind':match[1].casefold(),'value':float(match[2].replace(',','.')),'unit':unit,'basis':taxes+' / '+('je kWh' if match[1].casefold()=='arbeitspreis' else period),'text':area.strip()[:400]})
    return claims[:6]


def review(store,question,hits,as_of):
    """Re-read original local text for selected evidence only. No network or model calls."""
    kind=topic(question)
    if not kind or re.search(r'führerschein|fuehrerschein|fahrerlaubnis',question,re.I):return hits,{}
    originals=store.full_evidence([hit['item_id'] for hit in hits[:16]])
    rows=[];notes=[]
    for hit in hits[:16]:
        original=originals.get(hit['item_id'],hit)
        row={**hit,**original,'document_date':original.get('document_date') or hit.get('document_date')}
        if not acceptable(question,row):continue
        full=message_text(row.get('body',''))
        section=read_sections(full,question)
        valid=effective_date(full) if kind in ('leave','tariff') else None
        note={'source_id':row['item_id'],'document_date':row.get('document_date'),'effective_from':valid.isoformat() if valid else None,'future':bool(valid and valid>as_of),'validity':'future' if valid and valid>as_of else 'effective_date_reached_scope_unverified' if valid else 'unknown_or_multiple_dates','amendment_candidate':bool(re.search(r'nachtrag|änderungsvertrag',row['title'],re.I)),'section_read':len(full)>len(section),'content_missing':not full.strip(),'has_topic_section':bool(re.search(ANCHORS[kind],full,re.I)),'date_basis':'Explicit clause dates only; document_date is not a validity date.'}
        if kind=='leave':note['claims']=[{'kind':'annual_leave',**claim} for claim in leave_claims(section)]
        if kind=='tariff':note['claims']=tariff_claims(section)
        if kind=='maintenance':note['maintenance_dates']=[{'date':fact[0].date().isoformat(),'basis':fact[1]} for fact in maintenance_facts({**row,'body':full})]
        row['body']=section;row['evidence_section']=True
        rows.append(row);notes.append(note)
    if kind=='leave':
        by_id={note['source_id']:note for note in notes}
        rows.sort(key=lambda row:(not by_id[row['item_id']]['future'],bool(by_id[row['item_id']].get('claims')),by_id[row['item_id']]['effective_from'] or ''),reverse=True)
    retained={row['item_id'] for row in rows[:8]}
    notes=[note for note in notes if note['source_id'] in retained]
    claims=[{**claim,'source_id':note['source_id']} for note in notes if not note['future'] for claim in note.get('claims',[])]
    conflicts=[]
    groups={}
    for claim in claims:groups.setdefault((claim['kind'],claim['unit'],claim.get('basis','')),[]).append(claim)
    for (claim_kind,unit,basis),group in groups.items():
        if len({claim['value'] for claim in group})>1:conflicts.append({'kind':'different_'+claim_kind+'_values','unit':unit,'basis':basis,'claims':group,'requires_scope_and_validity_check':True})
    return rows[:8],{'topic':kind,'as_of':as_of.isoformat(),'documents':notes,'conflicts':conflicts,'scope_warning':'Local hints require verification against source text, person, employer, working week and effective date. Newer scans do not override old contracts.'}
