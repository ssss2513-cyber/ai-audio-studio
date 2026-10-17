"""Encrypted credentials, exclusive key leases, and durable quota cooldowns."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
from zoneinfo import ZoneInfo
from .provider import ProviderError, ProviderCancelled

MAX_KEYS=10
MAX_KEY_LENGTH=2048


def next_daily_reset():
    zone=ZoneInfo('America/Los_Angeles')
    tomorrow=dt.datetime.now(zone).date()+dt.timedelta(days=1)
    return dt.datetime.combine(tomorrow,dt.time(),zone).timestamp()+60


class PoolUnavailable(ProviderError):
    def __init__(self,retry_at=0,scope='generation',keys=None):
        labels={'daily':'일일 한도','rate':'분당 한도','quota_zero':'허용량 0','auth':'인증·권한 오류',
                'temporary':'일시 연결 오류','disabled':'사용 해제','shared':'공유 프로젝트 대기'}
        counts={status:sum(k['status']==status for k in keys or []) for status in labels}
        summary=(f"등록 {len(keys)}개 중 "+', '.join(f'{labels[k]} {v}개' for k,v in counts.items() if v)+'. ') if keys else ''
        super().__init__('지금 분석에 사용할 수 있는 API 키가 없습니다. '+summary+'저장된 분석은 유지됩니다. '+
            ('한도 대기 시간이 지나면 자동으로 재개합니다.' if retry_at else 'AI 설정에서 키 상태를 확인하거나 사용 가능한 키를 추가해 주세요.'),
            code=429,provider_status='KEY_POOL_UNAVAILABLE',retryable=False)
        self.retry_at=retry_at;self.scope=scope


class KeyPool:
    def __init__(self,core,library,factory):
        self.core=core;self.lib=library;self.factory=factory
        self.lock=threading.RLock();self.changed=threading.Condition(self.lock)
        self.busy=set();self.cursor=0;self.model_cache={}

    def records(self):
        with self.lock:
            cfg=self.lib.config()
            if 'api_keys' in cfg:return cfg['api_keys']
            encrypted=cfg.get('key_encrypted')
            key=os.getenv('GEMINI_API_KEY','')
            if not key and encrypted:key=self.core.dec(encrypted)
            if not key:return []
            row=dict(id=secrets.token_hex(12),label='키 1',project='',enabled=True,
                secret=self.core.enc(key),fingerprint=hashlib.sha256(key.encode()).hexdigest(),states={})
            blocked=cfg.get('queue_blocked','')
            if '일일' in blocked and '429' in blocked:
                row['states']['generation']=dict(reason='daily',until=next_daily_reset(),error='기존 키의 일일 사용량 한도 소진')
            self.lib.configure(api_keys=[row])
            return [row]

    def save(self,items,parallelism):
        """Blank fields retain secrets; deletion and replacement are explicit per record."""
        if len(items)>2*MAX_KEYS:raise ValueError('API 키는 최대 10개까지 저장할 수 있습니다.')
        with self.changed:
            records=self.records();by_id={r['id']:r for r in records};seen=set();result=[]
            for item in items:
                identity=item.get('id','')
                if identity:
                    if identity not in by_id or identity in seen:raise ValueError('키 목록이 변경되었습니다. 새로고침 후 저장해 주세요.')
                    seen.add(identity);row=dict(by_id[identity])
                else:row=dict(id=secrets.token_hex(12),states={})
                if item.get('remove'):continue
                key=item.get('api_key','').strip()
                if key:
                    if len(key)<8 or len(key)>MAX_KEY_LENGTH or any(c.isspace() for c in key):
                        raise ValueError('API 키 형식을 확인해 주세요. 공백 없이 입력하세요.')
                    fingerprint=hashlib.sha256(key.encode()).hexdigest()
                    if row.get('fingerprint')!=fingerprint:row['states']={}
                    row.update(secret=self.core.enc(key),fingerprint=fingerprint)
                if not row.get('secret'):
                    if identity:raise ValueError('저장된 키를 읽지 못했습니다. 다시 입력해 주세요.')
                    continue
                project=item.get('project','').strip()
                shared=bool(item.get('share_project_quota',False))
                if shared and not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]',project):
                    raise ValueError('한도를 함께 관리하려면 표시 이름 대신 Google 프로젝트 ID를 입력해 주세요.')
                if not shared or project!=row.get('project'):
                    row['states']={scope:state for scope,state in row.get('states',{}).items() if state.get('origin')!='project'}
                row.update(label=(item.get('label','').strip() or f'키 {len(result)+1}')[:40],
                    project=project,share_project_quota=shared,enabled=bool(item.get('enabled',True)))
                result.append(row)
            # An omitted existing record is retained; a stale/incomplete form cannot silently erase keys.
            result.extend(r for r in records if r['id'] not in seen)
            if len(result)>MAX_KEYS:raise ValueError('API 키는 최대 10개까지 저장할 수 있습니다.')
            if len({r['fingerprint'] for r in result})!=len(result):raise ValueError('같은 API 키를 중복 등록할 수 없습니다.')
            # Project display names are not unique. Sharing requires an explicit project-ID confirmation.
            for row in result:
                if not row.get('share_project_quota') or not row.get('project'):continue
                for other in result:
                    if row['id']==other['id'] or self.group(row)!=self.group(other):continue
                    for scope,state in other.get('states',{}).items():
                        if state.get('reason') in ('daily','rate','quota_zero') and self.blocked(state):
                            current=row['states'].get(scope,{})
                            if not current or state.get('until',0)>current.get('until',0) or not state.get('until'):
                                row['states'][scope]=dict(state,origin='project',observed_key_id=state.get('observed_key_id',other['id']))
            self.lib.configure(api_keys=result,parallelism=max(1,min(MAX_KEYS,int(parallelism))))
            self.changed.notify_all()
            self.report('saved')

    @staticmethod
    def group(row):
        return 'project:'+row['project'] if row.get('share_project_quota') and row.get('project') else 'key:'+row['id']

    def repair_legacy_project_waits(self):
        """Recheck ambiguous copied waits once; keep independently recorded provider errors."""
        with self.changed:
            if self.lib.config().get('project_wait_provenance_v2'):return 0
            rows=self.records();groups={};changed=0
            for row in rows:
                if row.get('project') and not row.get('share_project_quota'):
                    for scope,state in row.get('states',{}).items():
                        if not state.get('origin') and state.get('reason') in ('daily','rate','quota_zero'):
                            signature=(row['project'],scope,json.dumps(state,sort_keys=True))
                            groups.setdefault(signature,[]).append(row)
            for (_,scope,_),members in groups.items():
                if len(members)<2:continue
                for row in members:
                    row['states'].pop(scope,None);row['quota_recheck_pending']=True;changed+=1
            self.lib.configure(api_keys=rows,project_wait_provenance_v2=True)
            self.changed.notify_all()
            print('LECTURE_KEY_REPAIR '+json.dumps(dict(ambiguous_waits_recheck=changed)),flush=True)
            return changed

    @staticmethod
    def blocked(state):
        return bool(state) and (not state.get('until') or state['until']>time.time())

    def available(self,row,scope):
        return row.get('enabled',True) and not self.blocked(row.get('states',{}).get('*')) and not self.blocked(row.get('states',{}).get(scope))

    def credential_info(self,row):
        # Only return length/type, never key characters or a reversible identifier.
        try:key=self.core.dec(row['secret'])
        except Exception:return dict(key_length=0,key_format='unknown',possible_truncation=False)
        kind='authorization' if key.startswith('AQ.') else 'standard' if key.startswith('AIza') else 'other'
        return dict(key_length=len(key),key_format=kind,
                    possible_truncation=kind=='authorization' and len(key)==256)

    def snapshot(self,scope='generation'):
        with self.lock:
            rows=self.records();result=[]
            for row in rows:
                states=row.get('states',{})
                state=next((s for s in (states.get('*'),states.get(scope)) if self.blocked(s)),{})
                blocked=self.blocked(state)
                result.append(dict(id=row['id'],label=row['label'],project=row.get('project',''),
                    share_project_quota=bool(row.get('share_project_quota')),quota_recheck_pending=bool(row.get('quota_recheck_pending')),
                    enabled=row.get('enabled',True),saved=True,busy=row['id'] in self.busy,
                    status='disabled' if not row.get('enabled',True) else 'shared' if blocked and state.get('origin')=='project' else state.get('reason','ready') if blocked else 'ready',
                    reason=state.get('reason',''),origin=state.get('origin','legacy' if blocked else ''),
                    checked_at=state.get('checked_at',0),code=state.get('code'),quota=state.get('quota',[]),
                    retry_at=state.get('until',0) if blocked else 0,error=state.get('error','') if blocked else '',
                    **self.credential_info(row)))
            return result

    def report(self,event):
        keys=self.snapshot();groups={};rows=self.records()
        details=[]
        for i,(key,row) in enumerate(zip(keys,rows)):
            group=groups.setdefault(self.group(row),len(groups)+1)
            details.append({k:key[k] for k in ('id','status','origin','reason','retry_at','code','quota','key_length','key_format','possible_truncation')})
            details[-1].update(slot=i+1,group=group,shared=bool(row.get('share_project_quota')))
        print('LECTURE_KEYS '+json.dumps(dict(event=event,configured=len(keys),
            available=sum(k['status']=='ready' for k in keys),parallel_limit=self.capacity(),keys=details)),flush=True)

    def has_available(self,scope='generation'):
        with self.lock:return any(self.available(r,scope) for r in self.records())

    def capacity(self):
        rows=[r for r in self.records() if r.get('enabled',True)]
        groups={self.group(r) for r in rows}
        return max(1,min(MAX_KEYS,int(self.lib.config().get('parallelism',MAX_KEYS)),len(groups)))

    def acquire(self,scope,cancelled,excluded):
        with self.changed:
            while True:
                if cancelled and cancelled():raise ProviderCancelled()
                rows=self.records();busy_groups={self.group(r) for r in rows if r['id'] in self.busy}
                candidates=[r for r in rows if r['id'] not in excluded and self.available(r,scope)]
                free=[r for r in candidates if r['id'] not in self.busy and self.group(r) not in busy_groups]
                if free:
                    row=free[self.cursor%len(free)];self.cursor+=1;self.busy.add(row['id']);return row
                if candidates:
                    self.changed.wait(.5);continue
                retry=[]
                for row in rows:
                    if not row.get('enabled',True):continue
                    states=[s for name,s in row.get('states',{}).items() if name in ('*',scope) and self.blocked(s)]
                    if states and all(s.get('until') for s in states):retry.append(max(s['until'] for s in states))
                if not retry and any(self.available(row,scope) for row in rows):retry.append(time.time()+1)
                raise PoolUnavailable(min(retry) if retry else 0,scope,self.snapshot(scope))

    def release(self,identity):
        with self.changed:self.busy.discard(identity);self.changed.notify_all()

    @staticmethod
    def skippable(error):
        return error.code in (401,403,429,500,502,503,504,0) or 'API 키가 유효하지' in str(error)

    def failed(self,identity,scope,error,fingerprint=None):
        quota=' '.join(q.get('quotaId','')+' '+q.get('quotaMetric','') for q in error.quota).lower()
        reason='temporary';until=time.time()+max(120,error.retry_after or 0);target=scope
        if error.code in (401,403) or 'API 키가 유효하지' in str(error):reason='auth';until=0;target='*'
        elif error.code==429:
            if error.quota_kind=='zero' or any(q.get('quotaValue')=='0' for q in error.quota):reason='quota_zero';until=0
            elif 'perday' in quota or 'per_day' in quota or '일일' in str(error):reason='daily';until=next_daily_reset()
            else:reason='rate';until=time.time()+max(60,error.retry_after or 0)
        state=dict(reason=reason,until=until,error=str(error)[:1000],origin='provider',observed_key_id=identity,
                   checked_at=time.time(),code=error.code,quota=error.quota)
        with self.changed:
            rows=self.records();failed=next((r for r in rows if r['id']==identity),None)
            if not failed or (fingerprint and failed['fingerprint']!=fingerprint):return
            for row in rows:
                same_project=reason in ('daily','rate','quota_zero') and failed.get('share_project_quota') and self.group(row)==self.group(failed)
                if row['id']==identity or same_project:
                    existing=row.get('states',{}).get(target,{})
                    if row['id']!=identity and self.blocked(existing) and existing.get('origin')=='provider':continue
                    row.setdefault('states',{})[target]=dict(state,origin='provider' if row['id']==identity else 'project')
                    row['quota_recheck_pending']=False
            self.lib.configure(api_keys=rows);self.changed.notify_all()
        print('LECTURE_KEY '+json.dumps(dict(key_id=identity,event='skip',scope=target,reason=reason,retry_at=until,code=error.code,quota=error.quota)),flush=True)

    def call(self,operation,scope='generation',cancelled=None,on_event=None,validate=False):
        attempted=set()
        while True:
            row=self.acquire(scope,cancelled,attempted);identity=row['id'];attempted.add(identity)
            try:
                api=self.factory(self.core.dec(row['secret']))
                api.cancelled=cancelled;api.on_event=on_event;api.rotate_on_quota=True
                if validate:
                    cache_key=(identity,row['fingerprint'],api.model,api.embedding_model)
                    with self.lock:cached=self.model_cache.get(cache_key)
                    if cached and cached[0]>time.time():
                        api._models=cached[1];api._output_limits=cached[2]
                    else:
                        api.validate_models()
                        with self.lock:self.model_cache[cache_key]=(time.time()+900,getattr(api,'_models',None),getattr(api,'_output_limits',{}))
                value=operation(api)
                with self.changed:
                    rows=self.records()
                    for current in rows:
                        if current['id']==identity and current['fingerprint']==row['fingerprint']:
                            current['quota_recheck_pending']=False
                            # Preserve a group-wide limit learned by another key while this request ran.
                            state=current.get('states',{}).get(scope)
                            if state and not self.blocked(state):current['states'].pop(scope,None)
                    self.lib.configure(api_keys=rows)
                return value
            except ProviderError as error:
                if not self.skippable(error):raise
                self.failed(identity,scope,error,row['fingerprint'])
            finally:self.release(identity)

    def retry_key(self,identity):
        # Explicit administrator retry after billing/permissions are changed.
        with self.changed:
            rows=self.records();row=next((r for r in rows if r['id']==identity),None)
            if row is None:raise ValueError('등록된 키를 찾을 수 없습니다.')
            row['states']={};self.lib.configure(api_keys=rows);self.changed.notify_all()

