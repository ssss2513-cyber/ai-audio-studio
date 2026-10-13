'use strict';
const token=new URLSearchParams(location.hash.slice(1)).get('token')||'';
history.replaceState(null,'',location.pathname);
const form=document.querySelector('#setup-form'),error=document.querySelector('#setup-error');
if(!token){error.textContent='운영자 전용 초기 설정 링크로 들어와 주세요. 이미 등록했다면 아래 관리자 로그인을 이용하세요.';form.querySelector('button').disabled=true;}
form.onsubmit=async e=>{e.preventDefault();const b=form.querySelector('button'),d=new FormData(form);if(d.get('password')!==d.get('confirmation')){error.textContent='비밀번호가 서로 다릅니다.';return;}b.disabled=true;error.textContent='안전하게 등록하고 있습니다…';try{const r=await fetch('/api/setup',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,password:d.get('password'),confirmation:d.get('confirmation')})});const v=await r.json();if(!r.ok)throw new Error(typeof v.detail==='string'?v.detail:'입력한 정보를 확인해 주세요.');form.reset();location.replace('/admin#admin');}catch(err){error.textContent=err.message;b.disabled=false;}};
