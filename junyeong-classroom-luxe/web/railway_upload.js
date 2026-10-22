'use strict';
/* Optional Railway admin feature. No video keys, passwords, or member
   information are ever sent to GitHub or embedded in this file. */
(() => {
 // Bound memory/network usage and wait for every active request before reset.
 async function runPartQueue(numbers,transfer,onComplete,signal,concurrency=6){
   let next=0,firstError=null;
   async function worker(){
     while(!firstError&&!signal.aborted&&next<numbers.length){
       const number=numbers[next++];
       try{const bytes=await transfer(number);onComplete(bytes);}
       catch(error){if(!firstError)firstError=error;}
     }
   }
   await Promise.allSettled(Array.from({length:Math.min(Math.max(1,Math.min(9,concurrency)),numbers.length)},worker));
   if(firstError)throw firstError;
   if(signal.aborted)throw Error('업로드를 일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');
 }

 const MAX_BYTES=5*1024**3;
 const $el=(s,r=document)=>r.querySelector(s);
 let busy=false,scanning=false,controller=null;
 const statusUrl='/admin/railway/status';

 async function decorate(){
   if(scanning || busy || !S.member || S.member.role!=='admin' || S.tab!=='videos')return;
   const content=$el('#main');
   if(!content || !$el('.admin-video',content) && !$el('.admin-content',content))return;
   if($el('#railway-tools-bar',content))return;
   scanning=true;
   const top=$el('.admin-content .section-top',content);
   if(!top){scanning=false;return;}
   const wrapper=document.createElement('div');
   wrapper.id='railway-tools-bar';
   wrapper.className='panel';
   wrapper.innerHTML='<div class="section-top"><div><div class="eyebrow">PRIVATE VIDEO STORAGE</div><h2>영상 저장소</h2></div><span class="pill">회원 전용</span></div>'
    +'<p id="railway-status-detail" class="form-note">영상 저장소 연결을 확인하고 있어요.</p>'
    +'<div class="button-row"><button class="primary small" type="button" data-railway-open disabled>영상 업로드</button>'
    +'</div>';
   top.insertAdjacentElement('afterend',wrapper);
   try{
     const info=await api(statusUrl);
     if(!wrapper.isConnected)return;
     const text=$el('#railway-status-detail',wrapper),button=$el('[data-railway-open]',wrapper);
     if(info.connected){
       text.textContent='영상 저장소 연결됨 · 사이트에 등록된 영상 '+prettyBytes(info.registeredVideoBytes||0)
         +' · 영상은 전용 저장소에서 직접 재생됩니다.';
       if(info.migration?.total){
         text.textContent+=' · 이전 '+info.migration.complete+'/'+info.migration.total+'개 완료';
         const blocked=info.migration.items.filter(x=>x.state==='blocked').length;
         if(blocked)text.textContent+=' · 원본 연결 대기 '+blocked+'개';
       }
       button.disabled=false;
       // Old Drive video cards keep their id, play-list assignment and progress
       // when the matching full original file is uploaded and verified.
       for(const item of S.videos||[]){
         if(item.source!=='drive')continue;
         const card=[...content.querySelectorAll('.admin-video')].find(x=>x.querySelector('[data-play="'+item.id+'"]'));
         if(!card||card.querySelector('[data-railway-replace]'))continue;
         const actions=card.querySelector('.button-row');if(!actions)continue;
         const b=document.createElement('button');b.type='button';b.className='outline small';
         b.textContent='원본 파일로 직접 이전';b.dataset.railwayReplace=item.id;
         actions.prepend(b);
       }
     }else {
       text.textContent='영상 저장소 연결을 준비하고 있습니다. 잠시 후 다시 확인해 주세요.';
     }
   }catch(error){
     if(wrapper.isConnected)$el('#railway-status-detail',wrapper).textContent='영상 저장소 연결 상태를 확인하지 못했습니다. '+error.message;
   }finally{scanning=false;}
 }

 function uploadModal(replace=''){
   if(busy){toast('진행 중인 업로드를 완료하거나 일시정지해 주세요.');return;}
   const previous=(S.videos||[]).find(v=>v.id===replace);
   const label=replace?'기존 강의 영상 교체':'새 강의 업로드';
   openModal('<div class="eyebrow">영상 업로드</div><h2>'+label+'</h2>'
    +'<p>영상은 사이트 서버를 거치지 않고 전용 영상 저장소로 직접 업로드됩니다. 여러 영상을 한 번에 선택하면 순서대로 자동 업로드합니다. 동시 전송 수를 조절할 수 있습니다. 업로드 중에는 이 창을 유지해 주세요.</p>'
    +(replace?'<div class="notice"><b>기존 영상 연결만 변경합니다.</b><p>회원의 기존 강의 순서·승인 상태·시청 기록은 유지합니다. Google Drive 원본은 삭제하지 않습니다.</p></div>':'')
    +'<form id="railway-upload-form"><label class="field"><span>영상 파일 · MP4 또는 WebM · 여러 개 선택 가능</span>'
    +'<input type="file" name="file" accept="video/mp4,video/webm" '+(replace?'':'multiple')+' required></label>'
    +'<label class="field"><span>영상 제목</span><input type="text" name="title" required maxlength="120" value="'+esc(previous?.title||'')+'"></label>'
    +'<div id="railway-file-list"></div>'
    +'<label class="field"><span>동시 전송 수</span><select name="concurrency"><option value="3">3개 · 안정 모드</option><option value="6" selected>6개 · 기본</option><option value="9">9개 · 빠른 전송 시도</option></select></label>'
    +'<label class="check"><input type="checkbox" name="rights" required><span>이 영상을 회원에게 제공할 권한이 있습니다.</span></label>'
    +'<div class="form-error" role="alert" hidden></div>'
    +'<button type="submit" class="primary full">영상 업로드 시작</button></form>'
    +'<div id="railway-progress" hidden><div class="upload-track"><span></span></div>'
    +'<p id="railway-progress-text" role="status">준비 중입니다.</p>'
    +'<button id="railway-stop" type="button" class="outline small">전송 일시정지</button></div>'
    +'<p class="form-note">처음 등록하는 영상은 비공개로 저장됩니다. 영상 관리에서 공개 상태를 선택해 주세요.</p>');
   const form=$el('#railway-upload-form');form.dataset.replace=replace;
   form.elements.file.onchange=()=>{
     const files=[...form.elements.file.files];
     form.completedFiles=new Set();
     form.elements.title.required=files.length<=1;
     form.elements.title.closest('label').hidden=files.length>1;
     if(files.length===1&&!replace)form.elements.title.value=files[0].name.replace(/\.[^.]+$/,'').slice(0,120);
     $el('#railway-file-list').innerHTML=files.length>1
       ?'<p>선택한 '+files.length+'개 영상을 순서대로 자동 업로드합니다. 제목을 수정할 수 있습니다.</p>'+files.map((f,i)=>'<label class="field"><span>'+esc(f.name)+'</span><input data-batch-title="'+i+'" required maxlength="120" value="'+esc(f.name.replace(/\.[^.]+$/,'').slice(0,120))+'"></label>').join(''):'';
   };
   $el('#railway-stop').onclick=()=>{if(controller)controller.abort();};
 }

 async function sendPart(u,number,blob) {
   const signed=await api('/admin/railway/uploads/'+encodeURIComponent(u.id)+'/parts/'+number,'POST',{});
   const response=await fetch(signed.url,{method:'PUT',body:blob,signal:controller.signal,
     credentials:'omit',referrerPolicy:'no-referrer'});
   if(!response.ok)throw Error('영상 전송에 실패했습니다. HTTP '+response.status+' · 버킷 CORS 설정과 연결 상태를 확인해 주세요.');
 }

 async function uploadRailway(form) {
   if(busy)throw Error('이미 업로드 중입니다.');
   const files=[...form.elements.file.files];
   const file=files[0];
   if(!file)throw Error('업로드할 영상 파일을 선택하세요.');
   if(file.size>MAX_BYTES)throw Error('영상 파일은 최대 5GB입니다.');
   if(!crypto?.subtle)throw Error('HTTPS에서만 업로드할 수 있습니다.');
   const mime=file.type||(/\.webm$/i.test(file.name)?'video/webm':'video/mp4');
   if(!/^video\/(mp4|webm)$/.test(mime))throw Error('MP4 또는 WebM 파일을 선택하세요.');
   if(!form.elements.rights.checked)throw Error('영상 제공 권한 확인이 필요합니다.');
   if(form.dataset.replace&&files.length!==1)throw Error('영상 교체는 한 개씩 선택해 주세요.');
   for(const f of files){
     if(f.size>MAX_BYTES||!f.size||!(/\.(mp4|webm)$/i.test(f.name)))throw Error(f.name+' · 5GB 이하 MP4 또는 WebM을 선택하세요.');
   }
   const completed=form.completedFiles||(form.completedFiles=new Set());
   const failures=[];
   const concurrency=[3,6,9].includes(Number(form.elements.concurrency.value))?Number(form.elements.concurrency.value):6;
   const inputs=[...form.querySelectorAll('input,select')];
   const errorNode=form.querySelector('.form-error');if(errorNode)errorNode.hidden=true;
   inputs.forEach(x=>x.disabled=true);

   busy=true;uploadRunning=true;controller=new AbortController();
   const progress=$el('#railway-progress');progress.hidden=false;
   const info=$el('#railway-progress-text'),bar=$el('#railway-progress .upload-track span');
   const submit=form.querySelector('button[type=submit]');submit.disabled=true;
   try{
     for(let index=0;index<files.length;index++){
     const file=files[index];
     if(completed.has(file))continue;
     if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다. 다시 시작하면 완료된 영상은 건너뜁니다.');
     const mime=file.type||(/\.webm$/i.test(file.name)?'video/webm':'video/mp4');
     const prefix='['+(index+1)+'/'+files.length+'] '+file.name+' · ';
     try{
     info.textContent=prefix+'원본 파일을 확인하고 있습니다.';

     const fingerprintValue=await fingerprint(file);
     const duration=await fileDuration(file);
     if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다.');
     const upload=await api('/admin/railway/uploads','POST',{
       title:(files.length>1?form.querySelector('[data-batch-title="'+index+'"]').value:form.elements.title.value).trim(), description:'회원 전용 강의',
       filename:file.name,mime,size:file.size,fingerprint:fingerprintValue,
       duration,rights:true,replaceVideoId:form.dataset.replace||''
     });
     const u=await api('/admin/railway/uploads/'+encodeURIComponent(upload.id));
     if(u.fingerprint!==fingerprintValue || u.size!==file.size)throw Error('이전 업로드 파일과 현재 파일이 다릅니다.');
     const done=new Set(u.completed||[]);
     const chunk=u.chunkSize;
     let total=[...done].reduce((n,k)=>n+Math.min(chunk,Math.max(0,file.size-(k-1)*chunk)),0);
     const show=()=>{
       const percent=(total/file.size*100);
       bar.style.width=percent.toFixed(1)+'%';
       info.textContent='완료 '+completed.size+'/'+files.length+'개 · 동시 '+concurrency+'개 · '+prefix+percent.toFixed(1)+'% · '+prettyBytes(total)+' / '+prettyBytes(file.size)+' · 저장된 조각은 이어올리기 가능합니다.';
     };
     show();
     const remaining=Array.from({length:Math.ceil(file.size/chunk)},(_,i)=>i+1).filter(n=>!done.has(n));
     await runPartQueue(remaining,async number=>{
       const part=file.slice((number-1)*chunk,Math.min(number*chunk,file.size));
       for(let retry=0;retry<3;retry++){
         if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');
         try{await sendPart(upload,number,part);return part.size;}
         catch(error){
           if(controller.signal.aborted)throw Error('업로드를 일시정지했습니다. 같은 파일을 다시 선택하면 이어올릴 수 있습니다.');
           if(retry===2)throw error;
           await new Promise(ok=>setTimeout(ok,(retry+1)*1200));
         }
       }
     },bytes=>{total+=bytes;show();},controller.signal,concurrency);
     info.textContent='파일 조각을 검증하고 저장 중입니다. 잠시 기다려 주세요.';
     await api('/admin/railway/uploads/'+encodeURIComponent(upload.id)+'/finish','POST',{});
     completed.add(file);
     }catch(error){
       if(controller.signal.aborted)throw error;
       failures.push(file.name+': '+error.message);
     }
     }
     if(failures.length)throw Error(completed.size+'/'+files.length+'개 완료. 실패한 영상은 다시 시작하면 재시도합니다. '+failures.join(' / '));
     toast(files.length+'개 영상 업로드 완료 · 영상 관리에서 공개 상태를 선택해 주세요.');
     busy=false;uploadRunning=false;
     closeModal();S.tab='videos';await render();
   }finally{
     busy=false;uploadRunning=false;controller=null;submit.disabled=false;inputs.forEach(x=>x.disabled=false);
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
 document.addEventListener('click',async event=>{
   const b=event.target.closest('[data-railway-open],[data-railway-replace],[data-action="upload"]');
   if(!b)return;
   event.preventDefault();
   event.stopImmediatePropagation();
   if(!S.member||S.member.role!=='admin')return;
   if(busy){toast('진행 중인 업로드를 완료하거나 일시정지해 주세요.');return;}
   try{
     const storage=await api(statusUrl);
     if(!storage.connected)throw Error('영상 저장소 연결 설정이 필요합니다.');
     if(b.dataset.railwayReplace)uploadModal(b.dataset.railwayReplace);
     else uploadModal();
   }catch(error){toast(error.message);}
 },true);
 document.addEventListener('submit',event=>{
   if(event.target.id!=='railway-upload-form')return;
   event.preventDefault();event.stopImmediatePropagation();
   uploadRailway(event.target).catch(error=>{
     const node=document.querySelector('#railway-upload-form .form-error');
     if(node){node.hidden=false;node.textContent=error.message;}
     toast(error.message);
   });
 },true);
})();

