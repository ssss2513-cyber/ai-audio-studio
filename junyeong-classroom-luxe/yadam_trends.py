"""Server-side YouTube Data API trend samples. Not a YouTube-wide ranking."""
import json,os,re,threading,time
from datetime import datetime,timedelta,timezone
import requests
from fastapi import Request
from pydantic import BaseModel,Field
API="https://www.googleapis.com/youtube/v3/"
LOCK=threading.Lock()
TERMS=("야담 옛날이야기","조선 야담","민담 설화")
WORDS=("야담","민담","옛날이야기","옛이야기","설화","전설")
def call(key,path,**params):
    params["key"]=key
    try: r=requests.get(API+path,params=params,timeout=(5,16))
    except requests.RequestException: raise RuntimeError("YouTube 연결에 실패했습니다.") from None
    if r.status_code==403: raise RuntimeError("YouTube API 키 권한 또는 일일 할당량을 확인해 주세요.")
    if r.status_code==400: raise RuntimeError("YouTube Data API v3 사용 설정과 키를 확인해 주세요.")
    if r.status_code!=200: raise RuntimeError("YouTube 조회 서비스가 응답하지 않습니다.")
    try:return r.json()
    except ValueError:raise RuntimeError("YouTube 응답을 해석하지 못했습니다.") from None
def batches(seq,n=50):
    a=list(seq)
    for i in range(0,len(a),n):yield a[i:i+n]
def duration(s):
    m=re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",s or "")
    if not m:return 0
    h,mi,se=(int(x or 0) for x in m.groups())
    return h*3600+mi*60+se
def collect(key):
    now=datetime.now(timezone.utc);since=now-timedelta(days=30)
    ids=[]
    for q in TERMS:
        items=call(key,"search",part="snippet",q=q,type="video",order="viewCount",maxResults=50,
            publishedAfter=since.strftime("%Y-%m-%dT%H:%M:%SZ"),regionCode="KR",
            relevanceLanguage="ko",safeSearch="moderate",videoEmbeddable="true").get("items",[])
        for it in items:
            vid=(it.get("id") or {}).get("videoId")
            if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}",vid) and vid not in ids: ids.append(vid)
    videos=[]
    for b in batches(ids):
        videos+=call(key,"videos",part="snippet,statistics,contentDetails,status",id=",".join(b)).get("items",[])
    channels={}
    ch_ids={(x.get("snippet") or {}).get("channelId") for x in videos}
    for b in batches(sorted(ch_ids-{None,""})):
        for x in call(key,"channels",part="snippet,statistics",id=",".join(b)).get("items",[]):
            channels[x["id"]]=x
    ranked=[]
    for v in videos:
        status=v.get("status") or {}
        if not status.get("embeddable") or status.get("privacyStatus")!="public":continue
        sn=v.get("snippet") or {};stats=v.get("statistics") or {}
        channel=channels.get(sn.get("channelId"),{})
        chs=channel.get("statistics") or {};chsn=channel.get("snippet") or {}
        if chs.get("hiddenSubscriberCount") or duration((v.get("contentDetails") or {}).get("duration"))<240:continue
        try:
            published=sn.get("publishedAt","")
            when=datetime.fromisoformat(published.replace("Z","+00:00"))
            views=int(stats.get("viewCount",0));subs=int(chs.get("subscriberCount",0))
        except (ValueError,TypeError):continue
        if not since<=when<=now or views<1 or subs<1:continue
        context=" ".join((sn.get("title",""),sn.get("description","")[:400],sn.get("channelTitle",""),chsn.get("description","")[:220]))
        if not any(w in context for w in WORDS):continue
        vid=v.get("id","")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}",vid):continue
        ranked.append(dict(id=vid,title=sn.get("title","")[:170],channel=sn.get("channelTitle","")[:110],
            publishedAt=published,views=views,subscribers=subs,ratio=round(views/subs,3),
            thumbnail="https://i.ytimg.com/vi/"+vid+"/hqdefault.jpg",
            url="https://www.youtube.com/watch?v="+vid))
    ranked.sort(key=lambda v:(-v["ratio"],-v["views"],v["id"]))
    pool=[];counts={}
    for v in ranked:
        channel=v["channel"]
        if counts.get(channel,0)>=3:continue
        counts[channel]=counts.get(channel,0)+1
        v["rank"]=len(pool)+1
        pool.append(v)
        if len(pool)>=24:break
    return {"status":"ok","videos":pool,"fetchedAt":now.isoformat(),"sampleSize":len(videos),
        "method":"최근 30일 · 4분 이상 · 조회수 ÷ 채널 구독자 수",
        "note":"야담·민담 관련 검색 결과 표본의 비교이며 YouTube 전체 순위는 아닙니다."}
def install(core):
    app=core.app
    with core.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS yadam_integrations(name TEXT PRIMARY KEY,secret TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS yadam_trend_cache(name TEXT PRIMARY KEY,body TEXT NOT NULL,expires REAL NOT NULL)")
    def key():
        with core.db() as db:row=db.execute("SELECT secret FROM yadam_integrations WHERE name='youtube'").fetchone()
        if row:
            try:return core.dec(row["secret"])
            except Exception:return ""
        return os.getenv("YOUTUBE_API_KEY","").strip()
    def cache():
        with core.db() as db:row=db.execute("SELECT body,expires FROM yadam_trend_cache WHERE name='feed'").fetchone()
        if not row:return None,0
        try:return json.loads(row["body"]),row["expires"]
        except (ValueError,TypeError):return None,0
    def put_cache(body,seconds):
        with core.db() as db:
            db.execute("""INSERT INTO yadam_trend_cache(name,body,expires) VALUES('feed',?,?)
                ON CONFLICT(name) DO UPDATE SET body=excluded.body,expires=excluded.expires""",
                (json.dumps(body,ensure_ascii=False),time.time()+seconds))
    class Config(BaseModel):
        apiKey:str=Field(default="",max_length=200)
        clear:bool=False
    @app.get('/api/admin/yadam-config')
    def config(req:Request):
        core.require(req,admin=True)
        data,expires=cache()
        return {"configured":bool(key()),"fetchedAt":(data or {}).get("fetchedAt"),
            "candidateCount":len((data or {}).get("videos",[])),"expiresAt":expires}
    @app.post('/api/admin/yadam-config')
    def configure(req:Request,body:Config):
        admin=core.require(req,admin=True,write=True)
        core.rate(req,"yadam_key",10)
        if not body.clear:
            candidate=body.apiKey.strip()
            if not re.fullmatch(r"AIza[A-Za-z0-9_-]{30,100}",candidate):
                core.fail(422,"YouTube Data API 키 형식을 확인해 주세요.")
            try:call(candidate,"videos",part="snippet",id="dQw4w9WgXcQ")
            except RuntimeError as exc:core.fail(422,str(exc))
        with core.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if body.clear:db.execute("DELETE FROM yadam_integrations WHERE name='youtube'")
            else:
                db.execute("""INSERT INTO yadam_integrations(name,secret) VALUES('youtube',?)
                    ON CONFLICT(name) DO UPDATE SET secret=excluded.secret""",(core.enc(candidate),))
            db.execute("DELETE FROM yadam_trend_cache WHERE name='feed'")
            core.audit(db,admin["id"],"youtube","key_removed" if body.clear else "key_configured")
        return {"ok":True,"configured":bool(key())}
    @app.get('/api/public/yadam-trends')
    def trends(req:Request):
        api_key=key()
        if not api_key:
            return {"status":"needs_key","videos":[],
                "message":"운영자 화면에서 YouTube Data API 키를 연결해 주세요."}
        data,expiry=cache()
        if data and time.time()<expiry:return data
        if not LOCK.acquire(False):
            return {**data,"status":"stale"} if data else {"status":"refreshing","videos":[]}
        try:
            data,expiry=cache()
            if data and time.time()<expiry:return data
            try:fresh=collect(api_key)
            except RuntimeError as exc:
                if data and data.get("videos"):
                    put_cache({**data,"status":"stale","message":"마지막 수집 데이터를 표시합니다."},900)
                    return {**data,"status":"stale","message":"마지막 수집 데이터를 표시합니다."}
                error={"status":"error","videos":[],"message":str(exc)}
                put_cache(error,900)
                return error
            put_cache(fresh,6*3600)
            return fresh
        finally:LOCK.release()
