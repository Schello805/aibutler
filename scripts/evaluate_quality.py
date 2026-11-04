"""Synthetic quality regression. Default is offline; --llm explicitly calls configured provider."""
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.quality_eval import evaluate_case
from backend.evidence_quality import INSTRUCTIONS

async def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm',action='store_true',help='Zusätzlich eine kostenpflichtige Modellantwort je Dokumentfall mit ausschließlich erfundenen Testbelegen erzeugen.')
    parser.add_argument('--output',type=Path,help='Optionaler JSON-Bericht einschließlich synthetischer Modellantworten.')
    args=parser.parse_args();fixtures=json.loads((Path(__file__).resolve().parents[1]/'tests/fixtures/answer_quality.json').read_text())
    settings=None
    if args.llm:
        from backend.config import Settings
        settings=Settings(_env_file=os.environ.get('BUTLER_ENV_FILE','.env'))
        if not settings.llm_api_key or not settings.llm_model:parser.error('KI-Anbieter, API-Key und Modell zuerst konfigurieren.')
        settings=settings.model_copy(update={'allow_external_llm':True})
    reports=[]
    with tempfile.TemporaryDirectory(prefix='butler-quality-') as directory:
        for index,case in enumerate(fixtures['cases']):
            report,payload=evaluate_case(Path(directory)/str(index),case,fixtures['as_of'])
            if args.llm and payload:
                from backend.intelligence import generate
                started=perf_counter()
                try:
                    answer=await asyncio.wait_for(generate(settings,INSTRUCTIONS+' Beantworte die Testfrage in höchstens drei kurzen Sätzen auf Deutsch. Verwende Ziffern für Zahlen. Alle Quellen sind erfundene Testdaten.',payload),timeout=20)
                    report['answer']=answer
                    report['checks']['expected_answer_terms']=all(term.casefold() in (answer or '').casefold() for term in case['expected'].get('answer_terms',[]))
                    if case['expected'].get('answer_must_acknowledge_uncertainty'):
                        report['checks']['unresolved_conflict_acknowledged']=bool(re.search(r'unklar|widerspr|abweich|unterschied|nicht sicher|zuordn',answer or '',re.I))
                except Exception:report['checks']['model_available']=False
                report['model_seconds']=round(perf_counter()-started,3)
            report['passed']=all(report['checks'].values());reports.append(report)
            print(('OK   ' if report['passed'] else 'FAIL ')+report['name']+' · lokal '+str(report['seconds'])+' s'+(' · Modell '+str(report['model_seconds'])+' s' if 'model_seconds' in report else ''))
            for name,passed in report['checks'].items():
                if not passed:print('     '+name)
    output={'synthetic':True,'as_of':fixtures['as_of'],'model_tested':args.llm,'passed':sum(row['passed'] for row in reports),'total':len(reports),'cases':reports}
    if args.output:args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2))
    return 0 if all(row['passed'] for row in reports) else 1

if __name__=='__main__':raise SystemExit(asyncio.run(run()))
