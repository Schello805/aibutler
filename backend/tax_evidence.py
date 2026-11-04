"""Local tax-ID retrieval hints, not validation or automatic identity attribution."""
import re

LABEL=r'\b(?:steuer[\s-]*(?:id|identifikationsnummer)|steuerliche\s+identifikationsnummer|id\s*nr\.?)\b'
GENERIC=r'\bidentifikationsnummer\b'
FTS='("steueridentifikationsnummer"* OR "identifikationsnummer"* OR "idnr"* OR "steuerid" OR "steuer id")'

def tax_id_question(text):return bool(re.search(LABEL,text,re.I))

def usable_label(text,match):
    return not re.search(r'(?:ust|umsatzsteuer|wirtschafts|kunden?|auftrag(?:s)?|rechnung(?:s)?|artikel|patient(?:en)?)[ -]*$',text[max(0,match.start()-25):match.start()],re.I)

def tax_label(text):
    for match in re.finditer(LABEL,text,re.I):
        if usable_label(text,match):return match
    if re.search(r'bzst|bundeszentralamt|besteuer|\bsteuer\w*',text,re.I):
        for match in re.finditer(GENERIC,text,re.I):
            if usable_label(text,match):return match
    return None

def has_tax_identifier(text):
    if not tax_label(text):return False
    for match in re.finditer(LABEL+'|'+GENERIC,text,re.I):
        if not usable_label(text,match):continue
        nearby=text[match.end():match.end()+120]
        number=re.search(r'^[^\d]{0,60}(\d[\d -]{9,24}\d)(?!\d)',nearby)
        if number:
            prefix=nearby[:number.start(1)]
            if re.search(r'telefon|fax|konto|kundennummer|umsatzsteuer',prefix,re.I):continue
            digits=re.sub(r'\D','',number[1])
            if len(digits)==11 and len(set(digits))>1:return True
    return False

def tax_relevant(title,body):
    if re.search(r'newsletter|ratgeber|stellenangebot|\bjobs?\b',title,re.I):return False
    return bool(tax_label(title+'\n'+body))
