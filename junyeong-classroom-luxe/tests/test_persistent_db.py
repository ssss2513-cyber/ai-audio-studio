"""Persistence regressions. PostgreSQL lifecycle tests use an isolated CI database."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from persistent_db import Row, database_url, postgres_sql


def test_sql_preserves_literals_and_supports_large_video_sizes():
    sql = postgres_sql("INSERT OR IGNORE INTO videos(size,title) VALUES(?,'what?; 100%')", True)
    assert sql == "INSERT INTO videos(size,title) VALUES(%s,'what?; 100%%') ON CONFLICT DO NOTHING"
    assert 'BIGINT' in postgres_sql('CREATE TABLE t(size INTEGER, duration REAL)')
    assert 'DOUBLE PRECISION' in postgres_sql('CREATE TABLE t(size INTEGER, duration REAL)')
    assert 'CASE WHEN status' in postgres_sql("UPDATE lectures SET status=CASE WHEN status='queued' THEN 'paused' ELSE status END WHERE id=?")
    assert 'status END WHERE' in postgres_sql("UPDATE lectures SET status=CASE WHEN status='queued' THEN 'paused' ELSE status END WHERE id=?")


def test_rows_keep_mapping_and_positional_access():
    row = Row(['id','title'], ('one','정규 1강'))
    assert dict(row) == {'id':'one','title':'정규 1강'}
    assert row[1] == row['title']
    assert list(row) == ['one','정규 1강']


def test_configuration_rejects_wrong_backend(monkeypatch):
    monkeypatch.setenv('CLASSROOM_DATABASE_URL', 'sqlite:///temporary.db')
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        database_url()


def test_required_database_never_creates_empty_local_accounts(tmp_path):
    env = dict(os.environ, CLASSROOM_DATA_DIR=str(tmp_path), CLASSROOM_REQUIRE_DATABASE='true',
               CLASSROOM_DATABASE_URL='', DATABASE_URL='')
    result = subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Persistent database is required' in result.stderr
    assert not (tmp_path/'classroom.sqlite3').exists()


@pytest.mark.skipif(not os.getenv('TEST_POSTGRES_URL'), reason='isolated PostgreSQL required')
def test_redeploy_retains_account_video_key_and_lecture_evidence(tmp_path):
    # Two independent processes and data directories model replacing an entire
    # App Platform container, including the loss of secret.key and SQLite files.
    env = dict(os.environ, CLASSROOM_DATABASE_URL=os.environ['TEST_POSTGRES_URL'],
               CLASSROOM_REQUIRE_DATABASE='true', CLASSROOM_DATA_DIR=str(tmp_path/'first'))
    seed = '''
import time
import cloud_app
import app as a
from lecture_ai.store import Library
with a.db() as db:
    db.execute("INSERT INTO members(id,name,phone,lookup,password,role,state,created,consent,consent_text) VALUES(?,?,?,?,?,'admin','approved',?,?,?)",
        ('restart-admin',a.enc('운영자'),a.enc('restart@example.test'),a.digest('restart@example.test'),a.hash_password('restart-test-only'),time.time(),time.time(),'{}'))
    db.execute("INSERT INTO videos(id,title,description,size,mime,duration,created,source,external_id) VALUES(?,?,?,?,?,?,?,'railway',?)",
        ('restart-video','정규 1강','',3*1024**3,'video/mp4',600,time.time(),'lessons/'+'a'*32+'.mp4'))
    db.execute("INSERT INTO progress VALUES(?,?,?,?)",('restart-admin','restart-video',125,time.time()))
lib=Library(a.DATA/'lecture-ai')
lid=lib.add('분석 강의','2026-10-23','test.mp4')
lib.save_part(lid,0,[dict(start=0,end=10,title='해상도',text='환경설정 해상도 선택',transcript='환경설정 해상도 선택',screen_text='',timing='subtitle')])
lib.update(lid,status='ready')
lib.configure(restart_lecture=lid)
'''
    check = '''
import cloud_app
import app as a
from fastapi.testclient import TestClient
from lecture_ai.store import Library
cl=TestClient(a.app,base_url='http://localhost',headers={'Origin':a.ORIGIN})
r=cl.post('/api/login',json={'identifier':'restart@example.test','password':'restart-test-only','admin':True})
assert r.status_code==200,r.text
with a.db() as db:
    row=db.execute("SELECT * FROM videos WHERE id='restart-video'").fetchone()
    assert row['size']==3*1024**3 and row['title']=='정규 1강'
    assert db.execute("SELECT seconds FROM progress WHERE video='restart-video'").fetchone()[0]==125
lib=Library(a.DATA/'lecture-ai');lid=lib.config()['restart_lecture']
assert lib.parts_done(lid)=={0}
assert lib.search('해상도',allowed_ids=[lid])
assert not (a.DATA/'classroom.sqlite3').exists()
'''
    for directory, source in [('first',seed),('replacement',check)]:
        env['CLASSROOM_DATA_DIR'] = str(tmp_path/directory)
        result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
