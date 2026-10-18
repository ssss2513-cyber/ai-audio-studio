"""Conversation context for answers drawn only from saved lecture records."""
from __future__ import annotations
import re


FOLLOW_UP=re.compile(
    r'(그거|그것|그게|그걸|그런|그렇게|그러면|그럼|이거|이것|이게|이걸|이렇게|저거|저것|'
    r'방금|아까|앞서|앞에서|위에서|이전 답변|이전 설명|다음 단계|그 다음|'
    r'첫\s*번째|두\s*번째|세\s*번째|\d+\s*번|'
    r'^(?:좀\s*)?(?:더\s*|쉽게|자세히|다시|계속|이어서|예시|예를|예제))')


def conversation_context(turns,previous_questions,question,library,allowed_ids,lecture_id=None):
    """Client history is context, never evidence. Recheck every old source's access."""
    refs=list(dict.fromkeys(cid for turn in turns if turn.get('role')=='assistant'
                            for cid in turn.get('source_ids',[]) if type(cid) is int))[:60]
    known={row['id']:row for row in library.context_chunks(refs,allowed_ids,lecture_id)}
    history=[]
    for turn in turns[-6:]:
        role=turn.get('role');text=str(turn.get('text','')).strip()[:1200]
        if not text or role not in ('user','assistant'):continue
        ids=turn.get('source_ids',[]) if role=='assistant' else []
        if role=='assistant' and (not ids or any(cid not in known for cid in ids)):continue
        history.append(dict(role=role,text=text,source_ids=ids))
    if not history:
        history=[dict(role='user',text=q[:500],source_ids=[]) for q in previous_questions[-2:] if q.strip()]
    followup=bool(history and FOLLOW_UP.search(question.strip()))
    query=question.strip()
    previous_sources=[]
    if followup:
        last_user=next((t['text'] for t in reversed(history) if t['role']=='user'),'')
        last_answer=next((t for t in reversed(history) if t['role']=='assistant'),{})
        # Current question comes first so the keyword budget cannot omit it.
        query='\n'.join(x for x in (query,last_user[:450],last_answer.get('text','')[:450]) if x)
        previous_sources=[known[cid] for cid in last_answer.get('source_ids',[])[:8]]
    return history,query,previous_sources,followup


def merge_evidence(*groups,limit=24):
    result=[];seen=set()
    for group in groups:
        for row in group:
            if row['id'] in seen:continue
            result.append(row);seen.add(row['id'])
            if len(result)>=limit:return result
    return result
