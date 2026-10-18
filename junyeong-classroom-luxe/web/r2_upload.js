'use strict';
/* Optional Cloudflare R2 admin feature. No video keys, passwords, or member
   information are ever sent to GitHub or embedded in this file. */
(() => {
 const MAX_BYTES=5*1024**3;
 const $el=(s,r=document)=>r.querySelector(s);
 let busy=false,scanning=false,controller=null;
 const statusUrl='/admin/r2/status';

 async function decorate(){
   if(scanning || busy || !S.member || S.member.role!=='admin' || S.tab!=='videos')return;
   const content=$el('#main');
   if(!content || !$el('.admin-video',content) && !$el('.admin-content',content))return;
   if($el('#r2-tools-bar',content))return;
   scanning=true;
   const top=$el('.admin-content .section-top',content);
   if(!top){scanning=false;return;}
   const wrapper=document.createElement('div');
   wrapper.id='r2-tools-bar';
   wrapper.className='panel';
   wrapper.innerHTML='<div class="section-top"><div><div class="eyebrow">PRIVATE VIDEO STORAGE</div><h2>끊김 없는 영상 저장소 · R2</h2></div><span class="pill">무료 제공량 활용</span></div>'
    +'<p id="r2-status-detail" class="form-note">무료 저장소 연결을 확인하고 있어요.</p>'
    +'<div class="button-row"><button class="primary small" type="button" data-r2-open disabled>R2로 영상 업로드</button>'
    +'<a class="outline small" href="https://developers.cloudflare.com/r2/get-started/" target="_blank" rel="noopener noreferrer">연결 설명 보기 ↗</a></div>';
   top.insertAdjacentElement('afterend',wrapper);
   try{
     const info=await api(statusUrl);
     if(!wrapper.isConnected)return;
     const text=$el('#r2-status-detail',wrapper),button=$el('[data-r2-open]',wrapper);
     if(info.connected){
       text.textContent='R2 연결됨 · 사이트에 등록된 영상 '+prettyBytes(info.registeredVideoBytes||0)
         +' / 안전 예산 '+prettyBytes(info.freeBudgetBytes||0)
         +' · 외부 저장소의 다른 파일과 요청량은 별도입니다.';
       button.disabled=false;
       // Old Drive video cards keep their id, play-list assignment and progress
       // when the matching full original file is uploaded and verified.
       for(const item of S.videos||[]){
         if(item.source!=='drive')continue;
         const card=[...content.querySelectorAll('.admin-video')].find(x=>x.querySelector('[data-play="'+item.id+'"]'));
         if(!card||card.querySelector('[data-r2-replace]'))continue;
         const actions=card.querySelector('.button-row');if(!actions)continue;
         const b=document.createElement('button');b.type='button';b.className='outline small';
         b.textContent='이 강의를 R2로 교체';b.dataset.r2Replace=item.id;
         actions.prepend(b);
       }
     }else {
       text.textContent='아직 Cloudflare R2 연결 전입니다. 운영자는 Railway 환경변수에 R2 계정·버킷·접속 키를 등록해 주세요. 계정 정보는 이 페이지에 입력하지 않습니다.';
     }
   }catch(error){
     if(wrapper.isConnected)$el('#r2-status-detail',wrapper).textContent='R2 연결 상태를 확인하지 못했습니다. '+error.message;
   }finally{scanning=false;}
 }

 function uploadModal(replace=''){
   if(busy){toast('진행 중인 업로드를 완료하거나 일시정지해 주세요.');return;}
   const previous=(S.videos||[]).find(v=>v.id===replace);
   const label=replace?'기존 강의 영상 교체':'새 강의 업로드';
   openModal('<div class="eyebrow">CLOUDFLARE R2 · LARGE VIDEO</div><h2>'+label+'</h2>'
    +'<p>2GB 영상은 사이트 서버를 거치지 않고 Cloudflare R2로 직접 올라갑니다. 업로드 중에는 이 창을 유지해 주세요.</p>'
    +(replace?'<div class="notice"><b>기존 영상 연결만 변경합니다.</b><p>회원의 기존 강의 순서·승인 상태·시청 기록은 유지합니다. Google Drive 원본은 삭제하지 않습니다.</p></div>':'')
    +'<form id="r2-upload-form"><label class="field"><span>영상 파일 · MP4 또는 WebM</span>'
    +'<input type="file" name="file" accept="video/mp4,video/webm" required></label>'
    +'<label class="field"><span>영상 제목</span><input type="text" name="title" required maxlength="120" value="'+esc(previous?.title||'')+'"></label>'
    +'<label class="check"><input type="checkbox" name="rights" required><span>이 영상을 회원에게 제공할 권한이 있습니다.</span></label>'
    +'<div class="form-error" role="alert" hidden></div>'
    +'<button type="submit" class="primary full">R2 업로드 시작</button></form>'
    +'<div id="r2-progress" hidden><div class="upload-track"><span></span></div>'
    +'<p id="r2-progress-text" role="status">준비 중입니다.</p>'
    +'<button id="r2-stop" type="button" class="outline small">전송 일시정지</button></div>'
    +'<p class="form-note">Cloudflare 무료 제공량은 전체 계정 기준입니다. 처음 등록하는 영상은 비공개로 저장됩니다.</p>');
   const form=$el('#r2-upload-form');form.dataset.replace=replace;
   $el('#r2-stop').onclick=()=>{if(controller)controller.abort();};
 }

 async function sendPart(u,number,blob) {
   const signed=await api('/admin/r2/uploads/'+encodeURIComponent(u.id)+'/parts/'+number,'POST',{});
   const response=await fetch(signed.url,{method:'PUT',body:blob,signal:controller.signal,
     credentials:'omit',referrerPolicy:'no-referrer'});
   if(!response.ok)throw Error('R2 전송에 실패했습니다. HTTP '+response.status+' · 버킷 CORS 설정과 연결 상태를 확인해 주세요.');
 }

 async function uploadR2(form) {
   const file=form.elements.file.files[0];
   if(!file)throw Error('업로드할 영상 파일을 선택하세요.');
   if(file.size>MAX_BYTES)throw Error('영상 파일은 최대 5GB입니다.');
   if(!crypto?.subtle)throw Error('HTTPS에서만 업로드할 수 있습니다.');
   const mime=file.type||(/\.webm$/i.test(file.name)?'video/webm':'video/mp4');
   if(!/^video\/(mp4|webm)$/.test(mime))throw Error('MP4 또는 WebM 파일을 선택하세요.');
   if(!form.elements.rights.checked)throw Error('영상 제공 권한 확인이 필요합니다.');
   const startAt=performance.now();
   busy=true;uploadRunning=true;controller=new AbortController();
   const progress=$el('#r2-progress');progress.hidden=false;
   const info=$el('#r2-progress-text'),bar=$el('#r2-progress .upload-track span');
   const submit=form.querySelector('button[type=submit]');submit.disabled=true;
   try{
     info.textContent='원본 파일을 확인하고 있습니다.';
     const fingerprintValue=await fingerprint(file);
     const upload=await api('/admin/r2/uploads','POST',{
       title:form.elements.title.value.trim(), description:'Cloudflare R2 회원 전용 강의',
       filename:file.name,mime,size:file.size,fingerprint:fingerprintValue,
       duration:await fileDuration(file),rights:true,replaceVideoId:form.dataset.replace||''
     });
     const u=await api('/admin/r2/uploads/'+encodeURIComponent(upload.id));
     if(u.fingerprint!==fingerprintValue || u.size!==file.size)throw Error('이전 업로드 파일과 현재 파일이 다릅니다.');
     const done=new Set(u.completed||[]);
     const chunk=u.chunkSize;
     let total=[...done].reduce((n,k)=>n+Math.min(chunk,Math.max(0,file.size-(k-1)*chunk)),0);
     const show=()=>{
       const percent=(total/file.size*100);
       bar.style.width=percent.toFixed(1)+'%';
       info.textContent=percent.toFixed(1)+'% · '+prettyBytes(total)+' / '+prettyBytes(file.size)+' · 저장된 조각은 이어올리기 가능합니다.';
     };
     show();
     for(let number=1;number<=Math.ceil(file.size/chunk);number++){
       if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');
       if(done.has(number))continue;
       const part=file.slice((number-1)*chunk,Math.min(number*chunk,file.size));
       let sent=false;
       for(let retry=0;retry<3;retry++){
         try{await sendPart(upload,number,part);sent=true;break;}
         catch(error){
           if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');
           if(retry===2)throw error;
           await new Promise(ok=>setTimeout(ok,(retry+1)*1200));
         }
       }
       if(sent){total+=part.size;show();}
     }
     info.textContent='파일 조각을 검증하고 저장 중입니다. 잠시 기다려 주세요.';
     await api('/admin/r2/uploads/'+encodeURIComponent(upload.id)+'/finish','POST',{});
     toast('R2 업로드 완료 · 기존 강의 연결 상태를 확인해 주세요.');
     busy=false;uploadRunning=false;
     closeModal();S.tab='videos';await render();
   }finally{
     busy=false;uploadRunning=false;controller=null;submit.disabled=false;
   }
 }

 // Existing UI code redraws #main; enhancement is idempotent per render.
 const main=document.getElementById('main');
 let scheduled=false;
 function queue(){
   if(scheduled)return;scheduled=true;
   requestAnimationFrame(()=>{scheduled=false;decorate().catch(()=>{});});
 }
 if(main)new MutationObserver(queue).observe(main,{childList:true,subtree:false});
 document.addEventListener('click',event=>{
   const b=event.target.closest('[data-r2-open],[data-r2-replace]');
   if(!b)return;
   event.preventDefault();
   event.stopImmediatePropagation();
   if(b.dataset.r2Replace)uploadModal(b.dataset.r2Replace);
   else uploadModal();
 },true);
 document.addEventListener('submit',event=>{
   if(event.target.id!=='r2-upload-form')return;
   event.preventDefault();event.stopImmediatePropagation();
   uploadR2(event.target).catch(error=>{
     const node=document.querySelector('#r2-upload-form .form-error');
     if(node){node.hidden=false;node.textContent=error.message;}
     toast(error.message);
   });
 },true);
})();
