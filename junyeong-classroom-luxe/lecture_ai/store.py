"""Persistent lecture library. No web framework or provider SDK dependency."""
from __future__ import annotations
import array
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3

UTC = dt.timezone.utc
ANALYSIS_SECONDS = 30*60
LEGACY_ANALYSIS_SECONDS = 5*60


def now():
    return dt.datetime.now(UTC).isoformat(timespec='seconds')


def fingerprint_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 310000)
    return salt + ':' + digest.hex()


def verify_password(password, encoded):
    return secrets.compare_digest(fingerprint_password(password, encoded.split(':')[0]), encoded)


def tokens(text):
    words = re.findall(r'[가-힣a-z0-9]+', text.lower())
    result = []
    for w in words:
        if w in {'어떻게', '알려줘', '주세요', '설명', '해줘', '뭐야', '있나요'}:
            continue
        result.append(w)
        if re.search('[가-힣]', w):
            result.extend(w[i:i+2] for i in range(len(w)-1))
    return list(dict.fromkeys(result))


def normalize(values):
    if not values or not all(math.isfinite(float(x)) for x in values):
        raise ValueError('검색 벡터 형식이 올바르지 않습니다.')
    n = math.sqrt(sum(float(v)**2 for v in values)) or 1
    return array.array('f', (float(v)/n for v in values)).tobytes()


def clip_windows(duration, size=ANALYSIS_SECONDS, overlap=3):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('영상 길이를 확인할 수 없습니다.')
    return [(max(0, i*size-overlap), min(duration, (i+1)*size))
            for i in range(math.ceil(duration/size))]


def seconds(stamp):
    bits = stamp.replace(',', '.').split(':')
    return sum(float(v)*60**i for i, v in enumerate(reversed(bits)))


def read_transcript(text, name):
    """SRT/VTT retain real timing; plain text deliberately has no fabricated times."""
    text = text.lstrip('\ufeff').replace('\r\n', '\n')
    cues = []
    for block in re.split(r'\n\s*\n', text.strip()):
        lines = block.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r'\s*((?:\d+:)?\d{2}:\d{2}[.,]\d+)\s*-->\s*((?:\d+:)?\d{2}:\d{2}[.,]\d+)', line)
            if m:
                start, end = seconds(m[1]), seconds(m[2])
                body = re.sub('<[^>]*>', '', ' '.join(lines[i+1:])).strip()
                if end <= start or start < 0:
                    raise ValueError('자막 시간 범위가 올바르지 않습니다.')
                if body:
                    cues.append({'start':start,'end':end,'text':body})
                break
    if name.lower().endswith(('.srt', '.vtt')) and not cues:
        raise ValueError('자막에서 시간 정보를 찾지 못했습니다. SRT/VTT 형식을 확인해 주세요.')
    if not cues:
        # Text chunks overlap slightly, without pretending to know video time.
        return [{'start':None,'end':None,'title':name,'text':text[i:i+900],
                 'transcript':text[i:i+900],'screen_text':'','timing':'unknown'}
                for i in range(0, len(text), 800) if text[i:i+900].strip()]
    grouped, current = [], None
    for cue in cues:
        if current and (len(current['text'])+len(cue['text'])>900 or cue['end']-current['start']>60):
            grouped.append(current)
            current = None
        if current is None:
            current = dict(cue)
        else:
            current['end'] = max(current['end'], cue['end'])
            current['text'] += ' ' + cue['text']
    if current:
        grouped.append(current)
    return [dict(c, title=c['text'][:55], transcript=c['text'], screen_text='', timing='subtitle') for c in grouped]


class Library:
    def __init__(self, folder):
        self.root = Path(folder).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('media','temp','backups'):
            (self.root/name).mkdir(exist_ok=True)
        self.path = self.root/'library.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lectures(
              id TEXT PRIMARY KEY,title TEXT NOT NULL,lecture_date TEXT NOT NULL,
              created_at TEXT NOT NULL,filename TEXT NOT NULL,bytes INTEGER NOT NULL DEFAULT 0,
              fingerprint TEXT,sha256 TEXT,media TEXT,status TEXT NOT NULL,duration REAL DEFAULT 0,
              completed INTEGER DEFAULT 0,total INTEGER DEFAULT 0,error TEXT DEFAULT '',
              warning TEXT DEFAULT '',kind TEXT DEFAULT 'video',pause INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS lecture_hash ON lectures(sha256);
            CREATE TABLE IF NOT EXISTS parts(lecture_id TEXT NOT NULL,part INTEGER NOT NULL,
              payload TEXT NOT NULL,PRIMARY KEY(lecture_id,part),
              FOREIGN KEY(lecture_id) REFERENCES lectures(id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY AUTOINCREMENT,
              lecture_id TEXT NOT NULL,part INTEGER NOT NULL,ordinal INTEGER NOT NULL,
              start REAL,end REAL,title TEXT NOT NULL,text TEXT NOT NULL,transcript TEXT NOT NULL,
              screen_text TEXT NOT NULL,timing TEXT NOT NULL,embedding BLOB,embedding_model TEXT,
              UNIQUE(lecture_id,part,ordinal),
              FOREIGN KEY(lecture_id) REFERENCES lectures(id) ON DELETE CASCADE);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(chunk_id UNINDEXED,terms);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,role TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,at TEXT,kind TEXT,input_tokens INTEGER,output_tokens INTEGER);
            ''')
            columns={r[1] for r in db.execute('PRAGMA table_info(lectures)')}
            for name in ('phase','phase_at','model_used'):
                if name not in columns:db.execute(f"ALTER TABLE lectures ADD COLUMN {name} TEXT DEFAULT ''")
            for name,kind in (('retry_at','REAL'),('retry_count','INTEGER')):
                if name not in columns:db.execute(f'ALTER TABLE lectures ADD COLUMN {name} {kind} DEFAULT 0')
            if 'analysis_windows' not in columns:
                db.execute("ALTER TABLE lectures ADD COLUMN analysis_windows TEXT DEFAULT ''")
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
            os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def config(self):
        with self.connect() as db:
            return {r['key']:json.loads(r['value']) for r in db.execute('SELECT * FROM config')}

    def configure(self, **values):
        with self.connect() as db:
            db.executemany('INSERT INTO config VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                           [(k,json.dumps(v,ensure_ascii=False)) for k,v in values.items()])

    def lecture(self, lid):
        with self.connect() as db:
            r = db.execute('SELECT * FROM lectures WHERE id=?',(lid,)).fetchone()
        if not r:
            raise ValueError('강의를 찾을 수 없습니다.')
        return dict(r)

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT l.*,(SELECT COUNT(*) FROM chunks c WHERE c.lecture_id=l.id) AS chunks
                FROM lectures l ORDER BY lecture_date DESC,created_at DESC''')]

    def add(self, title, date, filename, size=0, fingerprint='', kind='video'):
        lid = secrets.token_hex(12)
        with self.connect() as db:
            db.execute('''INSERT INTO lectures(id,title,lecture_date,created_at,filename,bytes,fingerprint,status,kind)
              VALUES(?,?,?,?,?,?,?,?,?)''',(lid,title,date,now(),filename,size,fingerprint,'uploading',kind))
        return lid

    def update(self, lid, **values):
        allowed={'status','duration','completed','total','error','media','sha256','pause','warning','title','lecture_date','kind','phase','phase_at','model_used','retry_at','retry_count'}
        if not values or not set(values)<=allowed:
            raise ValueError('수정할 수 없는 항목입니다.')
        with self.connect() as db:
            db.execute('UPDATE lectures SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),lid])

    def save_part(self, lid, part, segments, model='', vectors=None):
        vectors = vectors or [None]*len(segments)
        if len(vectors)!=len(segments):
            raise ValueError('검색 벡터 수가 일치하지 않습니다.')
        with self.connect() as db:
            old = [r[0] for r in db.execute('SELECT id FROM chunks WHERE lecture_id=? AND part=?',(lid,part))]
            db.executemany('DELETE FROM search_index WHERE chunk_id=?',[(i,) for i in old])
            db.execute('DELETE FROM chunks WHERE lecture_id=? AND part=?',(lid,part))
            for i,(s,v) in enumerate(zip(segments,vectors)):
                body = s.get('text','')
                cursor=db.execute('''INSERT INTO chunks(lecture_id,part,ordinal,start,end,title,text,transcript,
                  screen_text,timing,embedding,embedding_model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (lid,part,i,s.get('start'),s.get('end'),s.get('title',''),body,s.get('transcript',''),
                   s.get('screen_text',''),s.get('timing','estimated'),normalize(v) if v else None,model if v else None))
                indexed=' '.join(tokens(s.get('title','')+' '+body+' '+s.get('transcript','')+' '+s.get('screen_text','')))
                db.execute('INSERT INTO search_index(chunk_id,terms) VALUES(?,?)',(cursor.lastrowid,indexed))
            db.execute('INSERT INTO parts VALUES(?,?,?) ON CONFLICT(lecture_id,part) DO UPDATE SET payload=excluded.payload',
                       (lid,part,json.dumps(segments,ensure_ascii=False)))
            done=db.execute('SELECT COUNT(*) FROM parts WHERE lecture_id=?',(lid,)).fetchone()[0]
            db.execute('UPDATE lectures SET completed=?,retry_at=0,retry_count=0 WHERE id=?',(done,lid))

    def parts_done(self, lid):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT part FROM parts WHERE lecture_id=?',(lid,))}

    def analysis_plan(self, lid, duration):
        """Pin clip boundaries so resuming can never reinterpret a saved part number."""
        if not math.isfinite(duration) or not 0<duration<=48*3600:
            raise ValueError('영상 길이가 지원 범위를 벗어났습니다.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM lectures WHERE id=?',(lid,)).fetchone()
            if not row:raise ValueError('강의를 찾을 수 없습니다.')
            if row['analysis_windows']:
                windows=[tuple(w) for w in json.loads(row['analysis_windows'])]
                if not windows or abs(windows[-1][1]-duration)>0.1:
                    raise ValueError('저장된 분석 구간과 영상 길이가 다릅니다. 원본을 확인해 주세요.')
                return windows
            done={r[0] for r in db.execute('SELECT part FROM parts WHERE lecture_id=?',(lid,))}
            # Legacy JSON caches already contain absolute timestamps from 5-minute clips.
            # Keep every old index through the last saved/cached part, including any gaps.
            cached=set()
            for path in (self.root/'temp').glob(lid+'-*.json'):
                index=path.stem.removeprefix(lid+'-')
                if index.isdigit():cached.add(int(index))
            keep=max(done|cached,default=-1)+1
            legacy=clip_windows(duration,size=LEGACY_ANALYSIS_SECONDS)
            if keep>len(legacy):
                raise ValueError('기존 분석 구간과 영상 길이가 다릅니다. 원본을 확인해 주세요.')
            windows=legacy[:keep]
            cursor=windows[-1][1] if windows else 0
            while cursor<duration:
                end=min(duration,cursor+ANALYSIS_SECONDS)
                windows.append((max(0,cursor-3),end));cursor=end
            db.execute('UPDATE lectures SET analysis_windows=?,duration=?,total=?,completed=? WHERE id=?',
                (json.dumps(windows),duration,len(windows),len(done),lid))
        print('LECTURE_PLAN '+json.dumps(dict(lecture_id=lid,segment_seconds=ANALYSIS_SECONDS,
            preserved_parts=len(done),preserved_cached_parts=len(cached-done),total=len(windows),
            remaining=len(windows)-len(done))),flush=True)
        return windows

    def analysis_progress(self, row):
        """Mixed 5/30-minute plans report elapsed coverage, not equal-weight part counts."""
        if not row.get('analysis_windows'):
            return 100*row.get('completed',0)/row['total'] if row.get('total') else 0
        windows=json.loads(row['analysis_windows']);done=self.parts_done(row['id'])
        covered=sum(end-(windows[i-1][1] if i else 0)
                    for i,(_,end) in enumerate(windows) if i in done)
        return min(100,100*covered/windows[-1][1]) if windows else 0

    def context_chunks(self, chunk_ids, allowed_ids, lecture_id=None, adjacent=False):
        """Fetch verified saved evidence; neighbors recover steps split across records."""
        ids=list(dict.fromkeys(cid for cid in chunk_ids if type(cid) is int and cid>0))[:60]
        if not ids or not allowed_ids:return []
        filters="l.status='ready' AND l.id IN ("+','.join('?' for _ in allowed_ids)+')'
        args=list(allowed_ids)
        if lecture_id:filters+=' AND l.id=?';args.append(lecture_id)
        select='''SELECT c.id,c.lecture_id,c.part,c.ordinal,c.start,c.end,c.title,c.text,
          c.transcript,c.screen_text,c.timing,l.title AS lecture_title,l.lecture_date,l.created_at,l.media
          FROM chunks c JOIN lectures l ON l.id=c.lecture_id WHERE '''+filters
        with self.connect() as db:
            by_id={r['id']:dict(r) for r in db.execute(select+' AND c.id IN ('+','.join('?' for _ in ids)+')',[*args,*ids])}
            result=[by_id[cid] for cid in ids if cid in by_id];seen=set(by_id)
            if adjacent:
                # Retrieve adjacent explanations in chronological order, including part boundaries.
                for anchor in result[:6]:
                    for operator,direction in (('<','DESC'),('>','ASC')):
                        rows=db.execute(select+f''' AND c.lecture_id=? AND
                          (c.part {operator} ? OR (c.part=? AND c.ordinal {operator} ?))
                          ORDER BY c.part {direction},c.ordinal {direction} LIMIT 1''',
                          [*args,anchor['lecture_id'],anchor['part'],anchor['part'],anchor['ordinal']])
                        for row in rows:
                            if row['id'] not in seen:result.append(dict(row));seen.add(row['id'])
            return result

    def search(self, question, vector=None, model='', lecture_id=None, limit=14, allowed_ids=None):
        terms=tokens(question)[:45]
        filters="l.status='ready'"
        args=[]
        if lecture_id:
            filters+=' AND l.id=?'
            args.append(lecture_id)
        if allowed_ids is not None:
            if not allowed_ids:
                return []
            filters += ' AND l.id IN (' + ','.join('?' for _ in allowed_ids) + ')'
            args.extend(allowed_ids)
        lexical,semantic=[],[]
        with self.connect() as db:
            if terms:
                match=' OR '.join('"'+v+'"' for v in terms)
                lexical=[r[0] for r in db.execute(f'''SELECT c.id FROM search_index f JOIN chunks c ON c.id=f.chunk_id
                  JOIN lectures l ON l.id=c.lecture_id WHERE search_index MATCH ? AND {filters}
                  ORDER BY bm25(search_index) LIMIT 100''',[match,*args])]
            if vector:
                q=array.array('f');q.frombytes(normalize(vector))
                cursor=db.execute(f'''SELECT c.id,c.embedding FROM chunks c JOIN lectures l ON l.id=c.lecture_id
                  WHERE {filters} AND c.embedding_model=? AND c.embedding IS NOT NULL''',[*args,model])
                scores=[]
                try:
                    import numpy as np
                except ImportError:
                    np=None
                while rows:=cursor.fetchmany(256):
                    compatible=[r for r in rows if len(r['embedding'])==len(q)*4]
                    if np is not None and compatible:
                        matrix=np.frombuffer(b''.join(r['embedding'] for r in compatible),dtype=np.float32).reshape(-1,len(q))
                        sims=matrix@np.asarray(q,dtype=np.float32)
                        scores.extend((float(score),r['id']) for score,r in zip(sims,compatible))
                    else:
                        for r in compatible:
                            a=array.array('f');a.frombytes(r['embedding'])
                            scores.append((sum(x*y for x,y in zip(a,q)),r['id']))
                semantic=[cid for score,cid in sorted(scores,reverse=True)[:100]]
            ranks={}
            for ranking in (lexical,semantic):
                for rank,cid in enumerate(ranking):
                    ranks[cid]=ranks.get(cid,0)+1/(60+rank)
            ids=sorted(ranks,key=ranks.get,reverse=True)[:70]
            if not ids:
                return []
            rows={r['id']:dict(r) for r in db.execute(f'''SELECT c.*,l.title AS lecture_title,l.lecture_date,
              l.created_at,l.media FROM chunks c JOIN lectures l ON l.id=c.lecture_id
              WHERE c.id IN ({','.join('?' for _ in ids)})''',ids)}
        result=[];counts={}
        # Keep evidence from multiple lectures; do not let one lecture monopolize retrieval.
        for cid in ids:
            row=rows[cid];lid=row['lecture_id']
            if counts.get(lid,0)>=6 and not lecture_id:
                continue
            counts[lid]=counts.get(lid,0)+1
            row.pop('embedding',None)
            result.append(row)
            if len(result)>=limit:
                break
        return result

    def export(self):
        with self.connect() as db:
            lectures=[dict(r) for r in db.execute("SELECT id,title,lecture_date,created_at,duration,kind,status FROM lectures WHERE status='ready'")]
            chunks=[dict(r) for r in db.execute('''SELECT c.id,c.lecture_id,c.part,c.ordinal,c.start,c.end,c.title,c.text,
              c.transcript,c.screen_text,c.timing FROM chunks c JOIN lectures l ON l.id=c.lecture_id WHERE l.status='ready' ''')]
        return {'format':'lecture-assistant-knowledge-v1','exported_at':now(),'lectures':lectures,'chunks':chunks}
