"""Local owner-only account and maintenance commands. Never expose these through HTTP."""
import argparse, getpass, secrets, time
from app import db, enc, digest, uid, hash_password, audit, DATA

def main():
    p=argparse.ArgumentParser(description='준영 클래스 운영자 초기 설정')
    p.add_argument('command',choices=['create-admin','reset-password','cleanup'])
    args=p.parse_args()
    if args.command=='cleanup':
        from pathlib import Path
        import shutil
        cutoff=time.time()-7*86400
        with db() as c:
            old=c.execute("SELECT id FROM uploads WHERE state='uploading' AND created<?",(cutoff,)).fetchall()
            for row in old:
                shutil.rmtree(DATA/'parts'/row['id'],ignore_errors=True)
                c.execute('DELETE FROM uploads WHERE id=?',(row['id'],))
            c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            c.execute('DELETE FROM rates WHERE expires<?',(time.time(),))
        print(f'7일 이상 미완료 업로드 {len(old)}개와 만료 세션을 정리했습니다. 등록 영상·회원은 삭제하지 않았습니다.')
        return
    ident=input('운영자 이메일' if args.command=='create-admin' else '대상 로그인 식별자(휴대폰 번호/운영자 이메일)')
    ident=ident.strip().lower()
    if args.command=='create-admin' and ('@' not in ident or len(ident)>200): raise SystemExit('올바른 이메일을 입력해 주세요.')
    if '@' not in ident:
        from app import phone
        ident=phone(ident)
    pwd=getpass.getpass('비밀번호 (8자 이상, 화면에 표시되지 않습니다): ')
    if pwd!=getpass.getpass('비밀번호 확인: '): raise SystemExit('비밀번호가 다릅니다.')
    hp=hash_password(pwd)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM members WHERE lookup=?',(digest(ident),)).fetchone()
        if args.command=='create-admin':
            if row: raise SystemExit('이미 등록된 식별자입니다. 자동으로 관리자 권한을 부여하지 않습니다.')
            mid=uid(); now=time.time()
            c.execute("INSERT INTO members(id,name,phone,lookup,password,role,state,created,consent,consent_text) VALUES(?,?,?,?,?,'admin','approved',?,?,?)",
                      (mid,enc('운영자'),enc(ident),digest(ident),hp,now,now,'{}'))
            audit(c,mid,mid,'create_admin')
            print('운영자 계정을 만들었습니다. /admin 에서 로그인하세요.')
        else:
            if not row: raise SystemExit('계정을 찾을 수 없습니다.')
            print('입력한 번호는 본인 소유 인증이 아닙니다. 운영자가 정당한 계정 소유자를 별도로 확인해야 합니다.')
            if input('소유자 확인을 마쳤으면 RESET 입력: ')!='RESET': raise SystemExit('취소했습니다.')
            c.execute('UPDATE members SET password=?,epoch=epoch+1,revision=revision+1 WHERE id=?',(hp,row['id']))
            c.execute('DELETE FROM sessions WHERE member=?',(row['id'],)); audit(c,'local-operator',row['id'],'password_reset')
            print('비밀번호를 변경하고 기존 접속을 종료했습니다. 승인·제한 상태는 유지했습니다.')

if __name__=='__main__': main()
