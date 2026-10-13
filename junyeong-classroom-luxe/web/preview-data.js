/* Read-only sample data. Never used by index.html or the running application. */
window.JC_DEMO=true;
window.demoAdmin={id:'sample-admin',name:'운영자',phone:'예시 이메일',role:'admin',state:'approved',revision:1,created:1780000000,consent:1780000000};
const exampleVideos=[
{id:'sample-1',title:'나의 이야기를 콘텐츠로 만드는 시간',description:'일상의 작은 아이디어를 나만의 콘텐츠로 차분히 풀어내는 클래스.',duration:3480,theme:'rose',state:'public',size:2147483648,revision:1,poster:0,created:1791900000,position:1},
{id:'sample-2',title:'시선을 사로잡는 영상의 첫인상',description:'조화로운 색과 구성으로 완성하는, 오래 기억되는 영상의 시작.',duration:2640,theme:'lilac',state:'public',size:1800000000,revision:1,poster:0,created:1791800000,position:2},
{id:'sample-3',title:'작은 시작을 꾸준한 성장으로',description:'나만의 속도를 찾아 오래 지속할 수 있는 창작 습관을 만들어 봐요.',duration:3120,theme:'sage',state:'public',size:1500000000,revision:1,poster:0,created:1791700000,position:3}
];
const sampleMembers=['pending','approved','approved','blocked','removed'].map((state,i)=>({id:'sample-member-'+i,name:'예시 회원 '+String(i+1).padStart(2,'0'),phone:'010-****-000'+i,state,role:'member',revision:1,created:1791600000+i*86400,consent:1791600000+i*86400}));
window.demoApi=async(path,method,body)=>{
if(method!=='GET')throw new Error('디자인 미리보기입니다. 실제 저장은 하지 않습니다.');
const [p,query]=path.split('?'),params=new URLSearchParams(query||'');
if(p==='/meta')return {title:'준영 클래스',contact:'ssss2513@gmail.com',retention:'회원 탈퇴 또는 개인정보 삭제 요청 처리 시까지',open:true,autoApprove:false,ready:true,revision:1};
if(p==='/session')return {member:window.demoSignedIn?window.demoAdmin:null,csrf:window.demoSignedIn?'preview':null};
if(p==='/catalogue'||p==='/videos'||p==='/admin/videos')return {items:exampleVideos};
if(p==='/admin/overview')return {counts:{pending:1,approved:2,blocked:1,removed:1},videos:3,recent:[{action:'approve',created:1791900000},{action:'upload_finish',created:1791800000},{action:'join',created:1791700000}]};
if(p==='/admin/members'){let a=sampleMembers.filter(m=>(!params.get('state')||m.state===params.get('state'))&&(!params.get('q')||m.name.includes(params.get('q'))));return {items:a,total:a.length,page:1};}
if(p.startsWith('/admin/members/'))return {...sampleMembers.find(m=>m.id===p.split('/').pop()),phone:'예시 연락처 · 실제 정보 아님'};
if(p==='/admin/uploads')return {items:[]};
if(p==='/admin/audits')return {items:[{action:'approve',subject:'sample-member-0',created:1791900000,reason:'화면 예시'},{action:'view_contact',subject:'sample-member-1',created:1791800000,reason:'화면 예시'}]};
throw new Error('미리보기에서 지원하지 않는 기능입니다.');
};
