"""Small model contexts; original evidence remains available locally in full."""
import re
from .retrieval import STOPWORDS,message_text


def excerpt(text,question,limit=2600):
    text=message_text(str(text))
    if len(text)<=limit:return text
    terms={word for word in re.findall(r'\w{3,}',question.casefold()) if word not in STOPWORDS}
    windows=[(index,text[index:index+650]) for index in range(0,len(text),600)]
    def score(row):
        lower=row[1].casefold()
        return sum(min(lower.count(term),3) for term in terms)+2*bool(re.search(r'leistungsdatum|arbeitsbeginn|arbeitspreis|grundpreis|erteil|abschlag',lower))
    chosen=sorted(sorted(windows[1:],key=score,reverse=True)[:3],key=lambda row:row[0])
    return (text[:650]+'\n[… gekürzter Beleg …]\n'+'\n'.join(row[1] for row in chosen))[:limit]


def compact_payload(payload):
    if not isinstance(payload,dict):return payload
    value=dict(payload);query=str(value.get('question',''))+' '+' '.join(value.get('retrieval_queries',[]))
    if isinstance(value.get('sources'),list):
        rows=value['sources'];chosen=[];seen=set()
        for row in rows:
            category=row.get('source','local')
            if category not in seen:chosen.append(row);seen.add(category)
        for row in rows:
            if len(chosen)>=6:break
            if row not in chosen:chosen.append(row)
        if value.get('evidence_review'):chosen=rows[:8]
        value['sources']=[{**row,'body':(str(row.get('body',''))[:6500 if index<2 else 2000] if row.get('evidence_section') else excerpt(row.get('body',''),query))} for index,row in enumerate(chosen[:8])]
    if isinstance(value.get('conversation'),list):value['conversation']=[{**row,'text':str(row.get('text',''))[:1200]} for row in value['conversation'][-4:]]
    if isinstance(value.get('activity_evidence'),list):value['activity_evidence']=[{**row,'body':excerpt(row.get('body',''),query,1200)} for row in value['activity_evidence'][:3]]
    personal=value.get('personal_live_context')
    if isinstance(personal,dict):
        personal=dict(personal)
        if personal.get('calendar'):
            calendar=dict(personal['calendar']);events=calendar.get('events',[])
            calendar['events']=[{**row,'description':str(row.get('description',''))[:250]} for row in events[:10]]
            calendar['truncated']=calendar.get('truncated',False) or len(events)>10
            personal['calendar']=calendar
        if isinstance(personal.get('contacts'),list):personal['contacts']=personal['contacts'][:6]
        value['personal_live_context']=personal
    if isinstance(value.get('smart_home_changes'),list):value['smart_home_changes']=value['smart_home_changes'][:8]
    if isinstance(value.get('context'),dict):
        context=dict(value['context']);projects=context.get('projects',[])
        context['projects']=[{**row,'notes':str(row.get('notes',''))[:400]} for row in projects[:8]]
        if len(projects)>8:context['other_project_names']=[row.get('name','') for row in projects[8:]]
        value['context']=context
    return value
