from __future__ import annotations
import contextlib
import datetime as dt
import json
import os
import threading
from fastapi import Request
from pydantic import BaseModel, Field
from .store import Library, now
from .worker import Worker
from .provider import Gemini, ProviderError, FALLBACK_MODEL, ANSWER_SYSTEM, ANSWER_SCHEMA


class ApiKeySpec(BaseModel):
    id: str=Field(default='',max_length=32)
    label: str=Field(default='',max_length=40)
    project: str=Field(default='',max_length=100)
    api_key: str=Field(default='',max_length=256)
    enabled: bool=True
    remove: bool=False


class Settings(BaseModel):
    api_key: str=Field(default='',max_length=256)
    keys: list[ApiKeySpec]|None=Field(default=None,max_length=20)
    parallelism: int=Field(default=10,ge=1,le=10)
    model: str=Field(default='gemini-3.8-flash',max_length=100)
    embedding_model: str=Field(default='gemini-embedding-2',max_length=100)
    fallback_model: str=Field(default=FALLBACK_MODEL,max_length=100)


class Action(BaseModel):
    action: str=Field(default='start',pattern='^(start|pause|rebuild|reindex)$')


class Question(BaseModel):
    question: str=Field(min_length=2,max_length=1600)
    lecture_id: str=Field(default='',max_length=64)
    previous_questions: list[str]=Field(default_factory=list,max_length=3)


def install(core,auth_headers):
    lib=Library(core.DATA/'lecture-ai')
    worker=Worker(core,lib,auth_headers)
    core.app.state.lecture_ai={'library':lib,'worker':worker}
    ask_slots=threading.BoundedSemaphore(2)
    old_lifespan=core.app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with old_lifespan(app):
            worker.start()
            try:yield
            finally:worker.stop.set();worker.wake.set()
    core.app.router.lifespan_context=lifespan

    def allowed(req,write=False):
        member=core.require(req,watch=True,write=write)
        clause="state NOT IN ('trash','purging')" if member['role']=='admin' else "state='public'"
        with core.db() as c:
            videos={r['id']:dict(r) for r in c.execute('SELECT * FROM videos WHERE '+clause)}
        return member,videos

    def configured():
        return any(k['enabled'] for k in worker.keys.snapshot())

    def safe_settings():
        cfg=lib.config()
        return dict(configured=configured(),model=cfg.get('model','gemini-3.8-flash'),
                    embedding_model=cfg.get('embedding_model','gemini-embedding-2'),
                    fallback_model=cfg.get('fallback_model',FALLBACK_MODEL),connection=cfg.get('connection',{}),
                    keys=worker.keys.snapshot(),parallelism=cfg.get('parallelism',10),max_keys=10)

    def queue(video,action):
        lid=video['id']
        if video['source']!='drive':core.fail(422,'기존 Google Drive 강의만 선택해 주세요.')
        with worker.lock:
            with lib.connect() as c:
                old=c.execute('SELECT * FROM lectures WHERE id=?',(lid,)).fetchone()
                if action=='pause':
                    if old:c.execute("UPDATE lectures SET pause=1,retry_at=0,status=CASE WHEN status IN ('queued','retrying') THEN 'paused' ELSE status END WHERE id=?",(lid,))
                    return 'paused'
                if not configured():core.fail(409,'AI 설정에서 Gemini API 키를 먼저 등록해 주세요.')
                if worker.active==lid or (old and old['status']=='analyzing'):
                    core.fail(409,'현재 구간을 분석하고 있습니다. 일시정지 후 구간 처리가 끝나면 다시 눌러 주세요.')
                if old and old['status']=='ready' and action=='start':return 'ready'
                if action=='reindex' and (not old or not old['total'] or old['completed']!=old['total']):
                    core.fail(409,'전체 영상 분석을 먼저 완료해 주세요.')
                if action=='rebuild' and old:
                    c.execute('DELETE FROM search_index WHERE chunk_id IN (SELECT id FROM chunks WHERE lecture_id=?)',(lid,))
                    c.execute('DELETE FROM lectures WHERE id=?',(lid,))
                    for p in (lib.root/'temp').glob(lid+'-*'):p.unlink(missing_ok=True)
                    old=None
                registered=dt.datetime.fromtimestamp(video['created'],dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
                if not old:
                    c.execute('''INSERT INTO lectures(id,title,lecture_date,created_at,filename,bytes,media,status,kind)
                        VALUES(?,?,?,?,?,?,?,'queued','video')''',
                        (lid,video['title'],registered,now(),video['title'],video['size'],video['external_id']))
                else:
                    c.execute("UPDATE lectures SET status='queued',pause=0,error='',phase='순서 대기',retry_at=0,retry_count=0,title=?,kind=? WHERE id=?",(video['title'],'reindex' if action=='reindex' or old['kind']=='reindex' else 'video',lid))
        lib.configure(queue_blocked='')
        worker.wake.set()
        return 'queued'

    @core.app.get('/api/ai/lectures')
    def lectures(req: Request):
        member,videos=allowed(req)
        rows={r['id']:r for r in lib.list()}
        admin=member['role']=='admin'
        items=[]
        for v in videos.values():
            if v['source']!='drive':continue
            r=rows.get(v['id'],{})
            item=dict(id=v['id'],title=v['title'],playlist=v['playlist'],duration=v['duration'],
                state=v['state'],status=r.get('status','new'),completed=r.get('completed',0),
                total=r.get('total',0),chunks=r.get('chunks',0))
            item['progress']=lib.analysis_progress(r) if r else 0
            if admin:item.update(error=r.get('error',''),warning=r.get('warning',''),pause=bool(r.get('pause')),
                phase=r.get('phase',''),phase_at=r.get('phase_at',''),model_used=r.get('model_used',''),retry_at=r.get('retry_at',0))
            if admin:
                with worker.lock:item['active_parts']=sum(lid==v['id'] for lid,_ in worker.inflight)
            items.append(item)
        return dict(items=items,configured=configured(),admin=admin,ready=sum(x['status']=='ready' for x in items),
                    queue_blocked=lib.config().get('queue_blocked','') if admin else '',
                    queue_retry_at=lib.config().get('provider_retry_at',0) if admin else 0,
                    keys=worker.keys.snapshot() if admin else [],parallelism=worker.keys.capacity() if admin else 0)

    @core.app.get('/api/admin/ai/settings')
    def get_settings(req: Request):
        core.require(req,admin=True)
        return safe_settings()

    @core.app.post('/api/admin/ai/settings')
    def set_settings(body: Settings,req: Request):
        member=core.require(req,admin=True,write=True)
        try:
            model=Gemini.model_name(body.model.strip());embed=Gemini.model_name(body.embedding_model.strip())
            fallback=Gemini.model_name(body.fallback_model.strip()) if body.fallback_model.strip() else ''
            values=dict(model=model,embedding_model=embed,fallback_model=fallback)
            if body.keys is not None:
                worker.keys.save([k.model_dump() for k in body.keys],body.parallelism)
                lib.configure(**values,connection=dict(ok=None,at=now(),message='키와 병렬 설정을 저장했습니다. 사용 가능 여부는 실제 분석 중 확인하며 한도가 소진된 키는 건너뜁니다.'))
                worker.wake_available_keys()
                with core.db() as c:core.audit(c,member['id'],'lecture-ai','ai_keys_saved',str(len(worker.keys.snapshot())))
                return safe_settings()
            key=body.api_key.strip() or worker.provider().key
            provider=Gemini(key,model,embed,worker.usage,fallback_model=fallback)
            provider.validate_models()
            if body.api_key.strip():values['key_encrypted']=core.enc(key)
            lib.configure(**values)
            records=worker.keys.snapshot()
            first=records[0] if records else {}
            worker.keys.save([dict(id=first.get('id',''),label=first.get('label','키 1'),
                project=first.get('project',''),api_key=body.api_key.strip() or key,enabled=True)],body.parallelism)
        except (ValueError,ProviderError) as e:core.fail(422,str(e))
        # Saving a valid key and successfully running the model are separate results.
        # A temporary service failure must not discard a correctly stored key.
        try:
            effective=provider.check_connection()
            connection=dict(ok=True,model=effective,at=now(),message='실제 AI 응답을 확인했습니다.')
            lib.configure(queue_blocked='')
            worker.wake.set()
        except ProviderError as error:
            connection=dict(ok=False,at=now(),message=str(error),code=error.code)
            if error.code in (401,403,429):lib.configure(queue_blocked=str(error))
        lib.configure(connection=connection)
        with core.db() as c:core.audit(c,member['id'],'lecture-ai','ai_settings')
        return safe_settings()

    @core.app.post('/api/admin/ai/keys/{key_id}/resume')
    def resume_key(key_id: str,req: Request):
        member=core.require(req,admin=True,write=True)
        try:worker.keys.retry_key(key_id)
        except ValueError as error:core.fail(404,str(error))
        worker.wake_available_keys()
        with core.db() as c:core.audit(c,member['id'],'lecture-ai','ai_key_resume',key_id)
        return safe_settings()

    @core.app.get('/api/admin/ai/models')
    def models(req: Request):
        core.require(req,admin=True)
        try:return worker.keys.call(lambda api:api.models(),scope='discovery')
        except ProviderError as e:core.fail(502,str(e))

    @core.app.post('/api/admin/ai/lectures/{lid}')
    def action(lid: str,body: Action,req: Request):
        member=core.require(req,admin=True,write=True)
        try:video=worker.video(lid)
        except ValueError as e:core.fail(404,str(e))
        status=queue(video,body.action)
        with core.db() as c:core.audit(c,member['id'],lid,'ai_'+body.action)
        return {'ok':True,'status':status}

    @core.app.post('/api/admin/ai/queue-new')
    def queue_new(req: Request):
        member=core.require(req,admin=True,write=True)
        if not configured():core.fail(409,'AI 설정에서 Gemini API 키를 먼저 등록해 주세요.')
        with core.db() as c:videos=[dict(r) for r in c.execute("SELECT * FROM videos WHERE source='drive' AND state NOT IN ('trash','purging')")]
        known={r['id'] for r in lib.list()}
        added=0
        for video in videos:
            if video['id'] not in known:queue(video,'start');added+=1
        with core.db() as c:core.audit(c,member['id'],'lecture-ai','ai_queue_new',str(added))
        return {'ok':True,'added':added}

    @core.app.post('/api/ai/ask')
    def ask(body: Question,req: Request):
        member,videos=allowed(req,True)
        core.rate(req,'ai-ask:'+member['id'],40)
        if body.lecture_id and body.lecture_id not in videos:core.fail(403,'현재 시청할 수 없는 강의입니다.')
        if not configured():core.fail(409,'운영자가 AI 연결을 준비하고 있습니다. 잠시 후 이용해 주세요.')
        if not ask_slots.acquire(False):core.fail(429,'다른 질문에 답하고 있습니다. 잠시 후 다시 질문해 주세요.')
        try:
            vector=None;embedding_model=lib.config().get('embedding_model','gemini-embedding-2')
            previous=[q[:500] for q in body.previous_questions]
            query='\n'.join(previous[-2:]+[body.question])
            try:vector,embedding_model=worker.keys.call(lambda api:(api.embed([query],query=True)[0],api.embedding_model),scope='embedding')
            except ProviderError:pass
            hits=lib.search(query,vector,embedding_model,body.lecture_id or None,limit=16,allowed_ids=list(videos))
            if not hits:
                return dict(supported=False,paragraphs=[dict(text='분석이 완료된 강의에서 관련 내용을 찾지 못했습니다. 강의 제목이나 사용한 용어를 함께 적어 주세요.',sources=[])],sources=[],followups=[])
            # Check access immediately before sending evidence, and again before returning it.
            _,current=allowed(req)
            hits=[r for r in hits if r['lecture_id'] in current]
            if not hits:core.fail(403,'강의 공개 상태가 변경되었습니다. 다시 질문해 주세요.')
            evidence=[dict(id=r['id'],lecture=current[r['lecture_id']]['title'],registered_date=r['lecture_date'],
                topic=r['title'],text=r['text'][:5000],transcript=r['transcript'][:3500],screen_text=r['screen_text'][:2000]) for r in hits]
            payload=dict(question=body.question,previous_questions=previous,EVIDENCE=evidence)
            answer=worker.keys.call(lambda api:api.generate([{'text':json.dumps(payload,ensure_ascii=False)}],
                ANSWER_SYSTEM+'\nregistered_date는 강의 등록일이며 실제 촬영일로 단정하지 마세요.',ANSWER_SCHEMA))
            _,current=allowed(req)
            if any(r['lecture_id'] not in current for r in hits):core.fail(403,'강의 공개 상태가 변경되었습니다. 다시 질문해 주세요.')
            valid={r['id']:r for r in hits};paragraphs=[];used=set()
            if answer.get('supported') is True:
                for part in answer.get('paragraphs',[])[:8]:
                    ids=part.get('sources',[])
                    if not ids or any(type(i) is not int or i not in valid for i in ids):continue
                    text=str(part.get('text','')).strip()[:5000]
                    if text:paragraphs.append(dict(text=text,sources=sorted(set(ids))));used.update(ids)
            if not paragraphs:
                return dict(supported=False,paragraphs=[dict(text='현재 강의 기록만으로는 정확하게 답하기 어렵습니다. 질문을 더 구체적으로 적거나 다른 강의를 선택해 주세요.',sources=[])],sources=[],followups=[])
            sources=[dict(id=r['id'],lecture_id=r['lecture_id'],title=current[r['lecture_id']]['title'],
                topic=r['title'],start=r['start'],end=r['end'],timing=r['timing'],excerpt=r['text'][:450]) for r in hits if r['id'] in used]
            return dict(supported=True,paragraphs=paragraphs,sources=sources,
                followups=[str(q)[:160] for q in answer.get('followups',[])[:2]])
        except ProviderError as e:core.fail(502,str(e))
        finally:ask_slots.release()

    return worker
