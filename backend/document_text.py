"""Bounded offline OCR for selected Paperless originals. Never calls a vision API."""
import csv
import io
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def class_excerpt(text):
    """Only class/date rows may leave local OCR for a driving-licence question."""
    rows=[];found=False
    for line in text.splitlines():
        match=re.match(r'^\s*(A1|A2|AM|A|B1|BE|B|C1E|C1|CE|C|D1E|D1|DE|D|L|T)\b',line,re.I)
        dates=re.findall(r'(?<!\d)\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\d)',line)
        if match and dates:
            row=match[1].upper()+' '+' | '.join(dates[:3])
            confidence=re.search(r'OCR-Mindestkonfidenz (\d+)',line)
            if confidence:row+=' [OCR-Mindestkonfidenz '+confidence[1]+']'
            rows.append(row);found=True
        elif re.search(r'\b9\b.*\b10\b.*\b11\b',line):rows.append('Erkannte Tabellenüberschriften: 9 / 10 / 11 / 12')
    return '\n'.join(rows)[:4000] if found else ''



def ocr_original(data):
    if not shutil.which('tesseract'):raise RuntimeError('Lokale Texterkennung fehlt. Bitte aibutler update ausführen.')
    from PIL import Image
    languages=subprocess.run(['tesseract','--list-langs'],capture_output=True,text=True,timeout=2,check=True).stdout.split()
    language='deu+eng' if 'deu' in languages else 'eng'
    deadline=time.monotonic()+7;pages=[];raw=[]
    if data.startswith(b'%PDF'):
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(data) as pdf:
            for index in range(min(len(pdf),2)):
                page=pdf[index]
                textpage=page.get_textpage();count=textpage.count_chars();text=textpage.get_text_range(count=min(count,100000)) if count else '';textpage.close()
                if class_excerpt(text):raw.append(text)
                else:
                    width,height=page.get_size()
                    if width*height*4<=16_000_000:pages.append(page.render(scale=2).to_pil().copy())
                page.close()
    else:
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>16_000_000:raise ValueError('Bild ist für lokale Texterkennung zu groß')
            image.seek(0);pages.append(image.convert('RGB'))
    with tempfile.TemporaryDirectory(prefix='butler-ocr-') as temp:
        for number,image in enumerate(pages,1):
            if image.width*image.height>16_000_000:continue
            for rotation in (0,90,180,270):
                remaining=deadline-time.monotonic()
                if remaining<.2:break
                path=Path(temp)/'page.png';image.rotate(rotation,expand=True).save(path)
                command=['tesseract',str(path),'stdout','-l',language,'--psm','6','tsv']
                try:output=subprocess.run(command,capture_output=True,text=True,timeout=min(2,remaining),check=True).stdout
                except subprocess.TimeoutExpired:continue
                except subprocess.CalledProcessError:raise RuntimeError('Lokale OCR-Sprachdateien fehlen oder Dokument konnte nicht gelesen werden.')
                grouped={}
                for word in csv.DictReader(io.StringIO(output),delimiter='\t',quoting=csv.QUOTE_NONE):
                    token=(word.get('text') or '').strip()
                    if not token:continue
                    key=(word['block_num'],word['par_num'],word['line_num'])
                    grouped.setdefault(key,[]).append((int(word['left']),token,float(word['conf'])))
                lines=[]
                for words in grouped.values():
                    ordered=sorted(words);plain=' '.join(word[1] for word in ordered)
                    if class_excerpt(plain) or re.search(r'\b9\b.*\b10\b.*\b11\b',plain):
                        confidence=min(word[2] for word in ordered)
                        lines.append(plain+' [OCR-Mindestkonfidenz '+str(round(confidence))+'; x='+','.join(str(word[0]) for word in ordered)+']')
                if class_excerpt('\n'.join(lines)):raw.append('Seite '+str(number)+'; Drehung '+str(rotation)+' Grad\n'+'\n'.join(lines));break
    result='\n'.join(raw)
    return result[:8000]
