// Kahwa «قهوة اليوم» DOM QA (ported from the pilot v4 qa.mjs). Usage: node qa.mjs <comp dir> <t1,t2,...> [shots dir]
// QA: seek the composition at given times, screenshot, and check every visible text box against Meta 9:16 safe zone,
// the lower-right action rail, the logo bug, overflow, overlaps between text boxes; collect console errors + map labels.
import { createRequire } from 'module';
const require = createRequire((process.env.NODE_PATH || process.cwd() + '/node_modules') + '/');
const puppeteer = require('puppeteer-core');
import http from 'http'; import fs from 'fs'; import path from 'path';
const dir=process.argv[2]; const times=process.argv[3].split(',').map(Number); const shots=process.argv[4]||''; if(shots) fs.mkdirSync(shots,{recursive:true});
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.ttf':'font/ttf','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.json':'application/json'};
const srv=http.createServer((q,r)=>{let u=decodeURIComponent(q.url.split('?')[0]); const f=path.join(dir,u==='/'?'index.html':u);
  fs.readFile(f,(e,d)=>{if(e){r.writeHead(404);r.end();return} r.writeHead(200,{'content-type':types[path.extname(f)]||'application/octet-stream'}); r.end(d)})}).listen(+process.env.PORT||8766,'127.0.0.1');
const b=await puppeteer.launch({executablePath:process.env.CHROME,args:['--no-sandbox','--use-angle=swiftshader','--font-render-hinting=none']});
const p=await b.newPage(); const errs=[]; p.on('console',m=>{if(m.type()==='error'||m.type()==='warning')errs.push(m.text())}); p.on('pageerror',e=>errs.push(String(e)));
await p.setViewport({width:1080,height:1920}); await p.goto('http://127.0.0.1:'+(process.env.PORT||8766)+'/',{waitUntil:'networkidle0'});
await p.evaluate(()=>document.fonts.ready);
const BANNED=["المغرب","الإمارات","الجزائر","إسرائيل","اسرائيل","Morocco","Algeria","Israel","UAE"];
const rep=[];
for(const t of times){
  const res=await p.evaluate((t,BANNED)=>{const tl=window.__timelines.main; tl.seek(t,false);
    const S={l:65,r:1015,t:269,b:1248}; const out=[]; const boxes=[];
    const vis=(el)=>{let a=el; while(a&&a.id!=='root'){const c=getComputedStyle(a); if(c.display=='none'||c.visibility=='hidden'||+c.opacity<0.05) return false; a=a.parentElement} return true};
    document.querySelectorAll('#root div, #root span, #root bdi').forEach(el=>{
      if(el.tagName==='BDI'&&el.parentElement&&el.parentElement.textContent.trim()!==el.textContent.trim()) return; const hasW=[...el.children].some(c=>c.classList&&c.classList.contains('w')); if(el.closest('.w')&&!el.classList.contains('w')) return; if(el.classList.contains('w')&&el.parentElement&&[...el.parentElement.children].some(c=>c.classList.contains('w'))) { /* word: checked via its line container */ return; } if(!hasW && [...el.childNodes].every(n=>n.nodeType!==3||!n.textContent.trim())) return;   // only nodes that own text
      if(!vis(el)) return; if(el.closest('[data-layout-ignore]')) {}
      let r=el.getBoundingClientRect(); let a=el.parentElement; while(a&&a.id!=='root'){const c=getComputedStyle(a); if(c.overflow!=='visible'){const pr=a.getBoundingClientRect(); r={left:Math.max(r.left,pr.left),right:Math.min(r.right,pr.right),top:Math.max(r.top,pr.top),bottom:Math.min(r.bottom,pr.bottom),width:1,height:1}} a=a.parentElement}
      if(r.right-r.left<2||r.bottom-r.top<2) return;
      const tx=el.textContent.trim().slice(0,40); const v=[]; const nc=!!el.closest('[data-nc]');
      if(!nc){ if(r.left<S.l)v.push('left'); if(r.right>S.r)v.push('right'); if(r.top<S.t)v.push('top'); if(r.bottom>S.b)v.push('bottom');
      if(r.bottom>1152&&r.right>853) v.push('rail'); }
      if(!nc) boxes.push({tx,r:[r.left,r.top,r.right,r.bottom]});
      const bug=[72,300,224,358]; if(!el.closest('.bug')&&!el.closest('.blogo')&&!(r.right<bug[0]||r.left>bug[2]||r.bottom<bug[1]||r.top>bug[3])) v.push('logo');
      if(el.scrollWidth>el.clientWidth+2 && getComputedStyle(el).overflow!=='visible') v.push('overflow');
      if(BANNED.some(x=>el.textContent.includes(x))) v.push('BANNED');
      if(v.length) out.push({text:tx,box:[r.left,r.top,r.right,r.bottom].map(Math.round),v});
    });
    for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i].r,b=boxes[j].r; const ox=Math.min(a[2],b[2])-Math.max(a[0],b[0]), oy=Math.min(a[3],b[3])-Math.max(a[1],b[1]); if(ox>4&&oy>4){const an=(x,y)=>x.contains?0:0; out.push({text:boxes[i].tx+' <> '+boxes[j].tx,box:[...a,...b].map(Math.round),v:['overlap']});}}
    const labs=[...document.querySelectorAll('svg.map text')].filter(x=>x.getAttribute('visibility')==='visible'&&vis(x.parentElement.parentElement)).map(x=>x.textContent);
    return {out,labs};
  },t,BANNED);
  const pair=res.labs.some(x=>x.includes('الصحراء'))&&!res.labs.includes('المغرب'); if(pair) res.out.push({text:'ESH without MAR label',box:[],v:['PAIR']});
  if(res.labs.some(x=>/إسرائيل|فلسطين/.test(x))) res.out.push({text:'merged shape labelled',box:[],v:['PSE']});
  rep.push({t,violations:res.out,mapLabels:res.labs});
  if(shots){ await new Promise(r=>setTimeout(r,60)); await p.screenshot({path:`${shots}/f_${t.toFixed(2)}.png`}); }
}
console.log(JSON.stringify({errors:errs.slice(0,10),rep},null,1)); await b.close(); srv.close();
