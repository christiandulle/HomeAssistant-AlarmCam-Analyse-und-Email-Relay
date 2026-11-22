"""Board: Webseite fuer die Seitenleiste von Home Assistant (Ingress).

Zeigt alle Mails der letzten Tage mit Status und Fehlergrund und erlaubt es,
gedrosselte oder gescheiterte Mails erneut zu senden. Die Seite lauscht nur auf der
internen Adresse des Add-ons und beantwortet nur Anfragen des Home-Assistant-
Supervisors (der vorher den Login pruefen laesst).
"""

import email.policy
import html
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from email.parser import BytesParser

import detect as _detect
import tune as _tune
from store import DET_FILTERS, FILTERS, PREVIEW_TYPE, CameraError, placeholder_name
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

log = logging.getLogger("relay")

SHOWN_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/mpeg",
}

STATUS = {
    "queued": ("wartet", "blue"),
    "sending": ("wird gesendet", "blue"),
    "sent": ("zugestellt", "green"),
    "failed": ("gescheitert", "red"),
    "throttled": ("gedrosselt", "amber"),
    "held": ("pr\u00fcfe Foto", "blue"),
    "filtered": ("ohne Erkennung", "amber"),
    "stored": ("Weiterleitung aus", "amber"),
    "discarded": ("verworfen", "grey"),
}

TABS = (
    ("open", "Nicht weitergeleitet"),
    ("all", "Alle"),
    ("problems", "Gescheitert"),
    ("throttled", "Gedrosselt"),
    ("filtered", "Ohne Erkennung"),
    ("stored", "Weiterleitung aus"),
    ("queued", "Wartend"),
    ("sent", "Zugestellt"),
)

LIGHTBOX = """
<div id="lb"><button class="x" id="lbx" title="Schlie&szlig;en (Esc)">&times;</button><button class="fvb" id="lbfav" title="Favorit (F)">&#9734;</button><button class="snd" id="lbsend" title="Foto erneut an das Ziel senden">Neu senden</button>
<div class="cap" id="lbc"></div>
<div class="stage"><button class="nav" id="lbp" title="Zur&uuml;ck (&larr;)">&#8249;</button>
<img id="lbi" alt=""><button class="nav" id="lbn" title="Weiter (&rarr;)">&#8250;</button></div>
<div class="ctl"><button id="lbplay" title="Abspielen / Pause (Leertaste)">&#9654;</button><button id="lbdir" title="Abspielrichtung umschalten"></button>
<button id="lbbx" title="Rahmen um erkannte Objekte ein-/ausblenden">Rahmen</button><span class="lbl">Tempo</span><input type="range" id="lbs" min="100" max="3000" step="100" value="800"
title="Anzeigedauer pro Foto"><span class="lbl" id="lbv">0,8 s</span></div>
<div class="ctl" id="lbrow" style="display:none"><span class="lbl">Label</span><button type="button" data-n="tier" title="Taste 1">Tier</button><button type="button" data-n="mensch" title="Taste 2">Mensch</button><button type="button" data-n="fahrzeug" title="Taste 3">Fahrzeug</button><button type="button" data-n="leer" title="Taste 4">Leer</button></div>
<div id="lbd"></div><div id="lbb"></div></div>
<script>
(function(){
var lb=document.getElementById('lb'),im=document.getElementById('lbi'),cap=document.getElementById('lbc');
var prev=document.getElementById('lbp'),next=document.getElementById('lbn'),pb=document.getElementById('lbplay');
var sel=document.getElementById('lbs'),list=[],pos=0,playing=false,timer=null,loading=false;
var dirb=document.getElementById('lbdir'),fwd=false;
try{fwd=localStorage.getItem('kameraDir')==='fwd';}catch(e){}
function dirBtn(){dirb.textContent=fwd?'alt \u2192 neu':'neu \u2192 alt';dirb.title=fwd?'Spielt zeitlich vorwaerts (vom aeltesten zum neuesten Foto). Klick: rueckwaerts':'Spielt zeitlich rueckwaerts (vom neuesten zum aeltesten Foto). Klick: vorwaerts';}dirBtn();
function moreB(){var m=document.getElementById('more');return m?m.getAttribute('data-b'):'';}
function cntLabel(){var c=document.getElementById('lbshow'),n=document.querySelectorAll('a.zoom').length;if(c)c.innerHTML='&#9654; '+n+(moreB()?'+':'')+' Foto'+(n===1&&!moreB()?'':'s');}
function loadMore(cb){var b=moreB();if(!b||loading){if(cb)cb();return;}loading=true;
var p=new URLSearchParams(location.search);p.set('b',b);p.set('frag','1');
var isG=!!document.querySelector('.grid');
var x=new XMLHttpRequest();x.open('GET',(isG?'./':'mails')+'?'+p.toString());
x.onload=function(){loading=false;if(x.status===200){var d=document.createElement('div');
d.innerHTML=isG?x.responseText:'<table><tbody>'+x.responseText+'</tbody></table>';
var g=document.querySelector('.grid')||document.querySelector('tbody'),m=document.getElementById('more'),f=d.querySelector('#fragmore');
[].slice.call(d.querySelectorAll(isG?'.gc':'tr')).forEach(function(c){g.appendChild(c);});
if(f&&m)m.setAttribute('data-b',f.getAttribute('data-b'));else if(m)m.parentNode.removeChild(m);
list=anchors();cntLabel();}
if(cb)cb();};x.onerror=function(){loading=false;if(cb)cb();};x.send();}
function loadAll(cb){if(moreB()&&!loading)loadMore(function(){loadAll(cb);});else cb();}
var lbv=document.getElementById('lbv'),dtx=document.getElementById('lbd');
var bxl=document.getElementById('lbb'),bxb=document.getElementById('lbbx'),fvb=document.getElementById('lbfav'),showBox=true;
try{showBox=localStorage.getItem('kameraBoxes')!=='0';}catch(e){}
var BC={mensch:'#c07a1a',tier:'#3f8f4a',fahrzeug:'#3a7bbf'},BN={mensch:'Mensch',tier:'Tier',fahrzeug:'Fahrzeug'};
function boxBtn(){bxb.style.opacity=showBox?'1':'.45';}boxBtn();
function boxHtml(t,test,low){var h='';(t||'').split(';').forEach(function(p){var a=p.split(':');if(a.length<6)return;
var x1=+a[2],y1=+a[3],x2=+a[4],y2=+a[5],c=low?'#8c8c8c':(BC[a[0]]||'#fff');
h+='<div style="position:absolute;left:'+(x1*100)+'%;top:'+(y1*100)+'%;width:'+((x2-x1)*100)+'%;height:'+((y2-y1)*100)+'%;border:1px '+(low?'dotted':(test?'dashed':'solid'))+' '+c+';box-sizing:border-box"><span style="position:absolute;left:-1px;'+(test?'bottom':'top')+':-20px;background:'+c+';color:#fff;font-size:12px;font-weight:'+(low?'500':'700')+';padding:1px 5px;border-radius:3px;white-space:nowrap">'+(test?'Test ':'')+(BN[a[0]]||a[0])+' '+Math.round(+a[1]*100)+' %'+(low?' (unter Schwelle)':'')+'</span></div>';});return h;}
function placeBoxes(){var t=bxl.getAttribute('data-b')||'',tt=bxl.getAttribute('data-tb')||'',lb=bxl.getAttribute('data-lb')||'',tlb=bxl.getAttribute('data-tlb')||'';
if((!t&&!tt&&!lb&&!tlb)||!showBox){bxl.style.display='none';return;}
var r=im.getBoundingClientRect();if(!r.width){bxl.style.display='none';return;}
var key=t+'|'+tt+'|'+lb+'|'+tlb;
if(bxl.getAttribute('data-done')!==key){bxl.innerHTML=boxHtml(lb,false,true)+boxHtml(tlb,true,true)+boxHtml(t,false)+boxHtml(tt,true);bxl.setAttribute('data-done',key);}
bxl.style.left=r.left+'px';bxl.style.top=r.top+'px';bxl.style.width=r.width+'px';bxl.style.height=r.height+'px';bxl.style.display='block';}
function label(){lbv.textContent=(parseInt(sel.value,10)/1000).toFixed(1).replace('.',',')+' s';}
try{var saved=parseInt(localStorage.getItem('kameraSpeed'),10);if(saved>=100&&saved<=3000)sel.value=saved;}catch(e){}
label();
function delay(){return parseInt(sel.value,10)||800;}
function schedule(){clearTimeout(timer);if(!playing)return;
timer=setTimeout(function(){var n=fwd?pos-1:pos+1;
if(n>=0&&n<list.length)show(n);
else if(!fwd&&moreB()){loadMore(function(){if(playing&&pos<list.length-1)show(pos+1);else stop();});}
else stop();},delay());}
function stop(){playing=false;clearTimeout(timer);pb.innerHTML='&#9654;';}
function begin(){playing=true;pb.innerHTML='&#10074;&#10074;';
if(fwd){if(pos<=0)show(list.length-1);else schedule();}
else{if(pos>=list.length-1)show(0);else schedule();}}
function play(){if(!list.length)return;
if(fwd&&moreB()){pb.textContent='\u2026';loadAll(function(){list=anchors();pos=Math.min(pos,list.length-1);begin();});}else begin();}
function setCap(i,c){var p=c.split('  -  '),title=p[0]||'',w=p.slice(1).join(' - '),m0=w.match(/\\(#\\d+\\)/),id=m0?m0[0]:'';
w=w.replace(id,'').trim();var m=w.match(/\\d{1,2}:\\d{2}(:\\d{2})?/),tm=m?m[0]:'',dt=m?w.replace(m[0],'').trim():w;
cap.textContent='';var d1=document.createElement('div'),d2=document.createElement('div');d2.className='ct';
d1.textContent='Foto '+(fwd?list.length-i:i+1)+' von '+list.length+(moreB()?'+':'')+' \u00b7 '+title+(dt?' \u00b7 '+dt:'')+(id?' '+id:'');d2.textContent=tm;
cap.appendChild(d1);if(tm)cap.appendChild(d2);}
function placeDet(){placeBoxes();var t=dtx.getAttribute('data-t')||'';if(!t&&dtx.getAttribute('data-low'))t='\\u200b';if(!t||Z.s!==1){dtx.style.display='none';return;}
var r=im.getBoundingClientRect();dtx.textContent=t;var lw=dtx.getAttribute('data-low')||'';if(lw){var sp=document.createElement('span');sp.className='lo';sp.textContent='\\n'+lw;dtx.appendChild(sp);}dtx.style.left=(r.left+8)+'px';dtx.style.top=(r.top+8)+'px';dtx.style.display='block';}
window.addEventListener('resize',placeDet);
var snb=document.getElementById('lbsend');
function sndBtn(a){var k=a.getAttribute('data-sk')||'',w=a.getAttribute('data-st')||'';
if(!a.getAttribute('data-id')){snb.style.display='none';return;}snb.style.display='';
if(k==='queued'||k==='sending'||k==='held'){snb.disabled=true;snb.textContent=w||'wartet';}
else{snb.disabled=false;snb.textContent=(k&&k!=='sent'?'Neu senden ('+w+')':'Neu senden');}}
function sendNow(){var a=list[pos];if(!a||snb.disabled)return;snb.disabled=true;snb.textContent='wird eingereiht ...';var x=new XMLHttpRequest();
x.open('POST','api/resend-photo/'+a.getAttribute('data-id'));x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
x.onload=function(){if(x.status===200){a.setAttribute('data-sk','queued');a.setAttribute('data-st','wartet');}sndBtn(a);
if(x.status!==200){snb.textContent='nicht m\u00f6glich';}};x.onerror=function(){sndBtn(a);};x.send('');}
var lrow=document.getElementById('lbrow');
function lblSet(a){return ((a&&a.getAttribute('data-lbl'))||'').split(',').filter(Boolean);}
function lblBtns(a){var on=a&&a.getAttribute('data-fav')==='1'&&a.getAttribute('data-id');lrow.style.display=on?'':'none';if(!on)return;var s=lblSet(a);
[].forEach.call(lrow.querySelectorAll('button'),function(b){b.className=s.indexOf(b.getAttribute('data-n'))>=0?'on':'';});}
function toggleLbl(n){var a=list[pos];if(!a||a.getAttribute('data-fav')!=='1'||!a.getAttribute('data-id'))return;
var s=lblSet(a),i=s.indexOf(n),old=a.getAttribute('data-lbl')||'';
if(n==='leer'){s=i>=0?[]:['leer'];}else{s=s.filter(function(x){return x!=='leer'&&x!==n;});if(i<0)s.push(n);}
a.setAttribute('data-lbl',s.join(','));lblBtns(a);
function undo(){a.setAttribute('data-lbl',old);if(list[pos]===a)lblBtns(a);}
var x=new XMLHttpRequest();x.open('POST','api/label/'+a.getAttribute('data-id'));x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
x.onload=function(){if(x.status!==200)undo();else{window.lblDirty=true;}};x.onerror=undo;x.send('v='+encodeURIComponent(s.join(',')));}
function favBtn(a){var f=a&&a.getAttribute('data-fav')==='1';fvb.innerHTML=f?'&#9733;':'&#9734;';fvb.style.color=f?'#ffd24a':'#fff';lblBtns(a);}
function setFav(a,v){a.setAttribute('data-fav',v?'1':'0');var old=a.querySelector('.fv');if(v&&!old){var sp=document.createElement('span');sp.className='fv';sp.innerHTML='&#9733;';a.appendChild(sp);}else if(!v&&old){a.removeChild(old);}}
function toggleFav(){var a=list[pos];if(!a||!a.getAttribute('data-id'))return;var want=a.getAttribute('data-fav')==='1'?0:1;
setFav(a,want);favBtn(a);var x=new XMLHttpRequest();x.open('POST','api/fav/'+a.getAttribute('data-id'));x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
x.onload=function(){if(x.status!==200){setFav(a,!want);favBtn(list[pos]);}};x.onerror=function(){setFav(a,!want);favBtn(list[pos]);};x.send('v='+want);}
function show(i){pos=i;resetZoom();var a=list[i];var dt=a.getAttribute('data-det')||'',tt=a.getAttribute('data-test')||'';sndBtn(a);dtx.setAttribute('data-t',tt?'Bisher: '+(dt||'-').replace(/\\n/g,', ')+'\\nTest: '+tt.replace(/\\n/g,', '):dt);favBtn(a);dtx.style.display='none';bxl.setAttribute('data-b',a.getAttribute('data-box')||'');bxl.setAttribute('data-tb',tt?(a.getAttribute('data-tbox')||''):'');bxl.setAttribute('data-lb',a.getAttribute('data-lbox')||'');bxl.setAttribute('data-tlb',a.getAttribute('data-tlbox')||'');dtx.setAttribute('data-low',a.getAttribute('data-low')||'');bxl.style.display='none';im.onload=im.onerror=function(){placeDet();schedule();};im.src=a.href;
setCap(i,a.getAttribute('data-cap')||'');
prev.disabled=i<=0;next.disabled=i>=list.length-1;
if(list[i+1]){(new Image()).src=list[i+1].href;}}
function anchors(){return [].slice.call(document.querySelectorAll('a.zoom'));}
function open(a){stop();try{fwd=localStorage.getItem('kameraDir')==='fwd';}catch(e){fwd=false;}dirBtn();list=anchors();show(list.indexOf(a));lb.style.display='flex';}
function startShow(){fwd=true;dirBtn();pb.textContent='\u2026';lb.style.display='flex';
loadAll(function(){list=anchors();if(!list.length){close();return;}playing=true;pb.innerHTML='&#10074;&#10074;';show(list.length-1);});}
function close(){stop();dtx.style.display='none';bxl.style.display='none';lb.style.display='none';im.onload=im.onerror=null;im.src='';if(window.onLbClose)window.onLbClose();}
function step(d){stop();if(lb.style.display==='flex'&&pos+d>=0&&pos+d<list.length)show(pos+d);}
document.addEventListener('click',function(ev){
var a=ev.target.closest&&ev.target.closest('a.zoom');
if(a){ev.preventDefault();open(a);return;}
if(ev.target.closest&&ev.target.closest('#lbshow')){startShow();return;}
if(ev.target===lb||ev.target.id==='lbx'||ev.target.classList.contains('stage')){close();}
else if(ev.target===prev){step(-1);}else if(ev.target===next){step(1);}
else if(ev.target===pb){if(playing)stop();else play();pb.blur();}
else if(ev.target===dirb){fwd=!fwd;try{localStorage.setItem('kameraDir',fwd?'fwd':'back');}catch(e){}dirBtn();setCap(pos,list[pos].getAttribute('data-cap')||'');schedule();dirb.blur();}
else if(ev.target.id==='lbmore'){loadMore();}
else if(ev.target.closest&&ev.target.closest('#lbrow button')){toggleLbl(ev.target.getAttribute('data-n'));ev.target.blur();}
else if(ev.target===fvb){toggleFav();fvb.blur();}else if(ev.target===snb){sendNow();snb.blur();}
else if(ev.target===bxb){showBox=!showBox;try{localStorage.setItem('kameraBoxes',showBox?'1':'0');}catch(e){}boxBtn();placeBoxes();bxb.blur();}});
sel.addEventListener('input',function(){label();try{localStorage.setItem('kameraSpeed',sel.value);}catch(e){}schedule();});
sel.addEventListener('change',function(){sel.blur();});
document.addEventListener('keyup',function(ev){if(ev.key===' '&&lb.style.display==='flex'&&ev.target!==sel)ev.preventDefault();});
document.addEventListener('keydown',function(ev){
if(lb.style.display!=='flex'||ev.target===sel)return;
if(ev.key==='Escape')close();else if(ev.key==='ArrowLeft')step(-1);else if(ev.key==='ArrowRight')step(1);
else if(ev.key==='f'||ev.key==='F'){toggleFav();}
else if(ev.key.length===1&&'1234'.indexOf(ev.key)>=0){toggleLbl(['tier','mensch','fahrzeug','leer'][+ev.key-1]);}else if(ev.key===' '){ev.preventDefault();if(playing)stop();else play();}});
var Z={s:1,x:0,y:0},g=null,lastTap=0;
function apply(){im.style.transform=Z.s===1&&!Z.x&&!Z.y?'':'translate('+Z.x+'px,'+Z.y+'px) scale('+Z.s+')';placeDet();}
function resetZoom(){Z.s=1;Z.x=0;Z.y=0;apply();}
function clampZ(){if(Z.s<=1){Z.x=0;Z.y=0;return;}
var mx=Math.max(0,(im.offsetWidth*Z.s-window.innerWidth)/2),my=Math.max(0,(im.offsetHeight*Z.s-window.innerHeight)/2);
Z.x=Math.min(mx,Math.max(-mx,Z.x));Z.y=Math.min(my,Math.max(-my,Z.y));}
function zoomAt(s2,fx,fy){s2=Math.min(8,Math.max(1,s2));var r=im.getBoundingClientRect(),
cx=r.left+r.width/2-Z.x,cy=r.top+r.height/2-Z.y,u=(fx-cx-Z.x)/Z.s,v=(fy-cy-Z.y)/Z.s;
Z.s=s2;Z.x=fx-cx-u*s2;Z.y=fy-cy-v*s2;clampZ();apply();}
function dist(t){return Math.hypot(t[0].clientX-t[1].clientX,t[0].clientY-t[1].clientY);}
function mid(t){return {x:(t[0].clientX+t[1].clientX)/2,y:(t[0].clientY+t[1].clientY)/2};}
function ctl(ev){return ev.target.closest&&ev.target.closest('.ctl,.nav,.x');}
lb.addEventListener('touchstart',function(ev){if(ctl(ev))return;var t=ev.touches;
if(t.length===2){stop();var m=mid(t);g={pinch:true,d:dist(t),s:Z.s,mx:m.x,my:m.y};ev.preventDefault();}
else if(t.length===1){g={x0:t[0].clientX,y0:t[0].clientY,sx:Z.x,sy:Z.y,moved:false,noswipe:false};}},{passive:false});
lb.addEventListener('touchmove',function(ev){if(!g)return;var t=ev.touches;
if(g.pinch&&t.length===2){ev.preventDefault();var m=mid(t);zoomAt(g.s*dist(t)/g.d,m.x,m.y);
Z.x+=m.x-g.mx;Z.y+=m.y-g.my;g.mx=m.x;g.my=m.y;clampZ();apply();}
else if(t.length===1&&!g.pinch){var dx=t[0].clientX-g.x0,dy=t[0].clientY-g.y0;
if(Math.abs(dx)>8||Math.abs(dy)>8)g.moved=true;
if(Z.s>1){ev.preventDefault();Z.x=g.sx+dx;Z.y=g.sy+dy;clampZ();apply();}}},{passive:false});
lb.addEventListener('touchend',function(ev){if(!g)return;var t=ev.touches;
if(g.pinch){if(Z.s<1.05)resetZoom();
if(t.length===1){g={x0:t[0].clientX,y0:t[0].clientY,sx:Z.x,sy:Z.y,moved:true,noswipe:true};}else g=null;return;}
if(t.length)return;var c=ev.changedTouches[0],now=Date.now();
if(!g.moved&&ev.target===im){if(now-lastTap<320){if(Z.s>1)resetZoom();else zoomAt(2.5,c.clientX,c.clientY);lastTap=0;ev.preventDefault();}
else lastTap=now;g=null;return;}
if(Z.s===1&&!g.noswipe){var d=c.clientX-g.x0;if(Math.abs(d)>50)step(d<0?1:-1);}
g=null;},{passive:false});
lb.addEventListener('gesturestart',function(ev){ev.preventDefault();});
lb.addEventListener('wheel',function(ev){if(lb.style.display!=='flex')return;ev.preventDefault();
zoomAt(Z.s*(ev.deltaY<0?1.2:1/1.2),ev.clientX,ev.clientY);},{passive:false});
im.addEventListener('dblclick',function(ev){ev.preventDefault();if(Z.s>1)resetZoom();else zoomAt(2.5,ev.clientX,ev.clientY);});
var drag=null;im.addEventListener('mousedown',function(ev){if(Z.s>1){drag={x:ev.clientX-Z.x,y:ev.clientY-Z.y};ev.preventDefault();}});
document.addEventListener('mousemove',function(ev){if(drag){Z.x=ev.clientX-drag.x;Z.y=ev.clientY-drag.y;clampZ();apply();}});
document.addEventListener('mouseup',function(){drag=null;});
var all=document.getElementById('selall');
if(all)all.addEventListener('change',function(){
[].forEach.call(document.querySelectorAll('input.sel:not(:disabled)'),function(c){c.checked=all.checked;});});
window.bulkSelected=function(){
var n=document.querySelectorAll('input.sel:checked').length;
if(!n){alert('Bitte zuerst Mails mit H\\u00e4kchen ausw\\u00e4hlen.');return false;}
return confirm(n+' ausgew\\u00e4hlte Mail(s) l\\u00f6schen?');};
})();
(function(){var t=document.getElementById('topid');if(!t||!(document.querySelector('.grid')||document.querySelector('table')))return;
var top=t.getAttribute('data-t'),box=null,isG=!!document.querySelector('.grid'),busy=false;
function popup(){var l=document.getElementById('lb');return l&&l.style.display==='flex';}
function typing(){var a=document.activeElement;return a&&/INPUT|SELECT|TEXTAREA/.test(a.tagName)||!!document.querySelector('input[name=ids]:checked');}
function check(){if(document.hidden||busy||popup())return;busy=true;
var p=new URLSearchParams(location.search);p.set('na',top);
var x=new XMLHttpRequest();x.open('GET',(isG?'./':'mails')+'?'+p.toString());
x.onload=function(){busy=false;if(x.status!==200)return;var n=0;try{n=JSON.parse(x.responseText).n||0;}catch(e){}
if(!n)return;
if((window.scrollY||0)<80&&!typing()){location.reload();return;}
if(!box){box=document.createElement('div');box.style.cssText='position:fixed;left:50%;bottom:16px;transform:translateX(-50%);background:#2563eb;color:#fff;padding:8px 16px;border-radius:20px;cursor:pointer;z-index:50;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,.4)';
box.onclick=function(){location.reload();};document.body.appendChild(box);}
box.textContent=n+(n>=100?'+':'')+(n===1?' neues Foto':' neue Fotos')+' – anzeigen';};
x.onerror=function(){busy=false;};x.send();}
setInterval(check,30000);})();
</script>
"""

CSS = """
.vsw{display:flex;gap:6px;margin:0 0 10px}.vsw a{padding:3px 10px;border:1px solid var(--line,#8886);border-radius:14px;text-decoration:none;color:inherit;font-size:13px}.vsw a.on{background:var(--accent,#2f6fed);color:#fff;border-color:transparent}
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1c1e;--mut:#6b7280;--line:#e5e7eb;--card:#f6f7f9}
@media(prefers-color-scheme:dark){:root{--bg:#1b1b1f;--fg:#e8e8ea;--mut:#9ca3af;--line:#33343a;--card:#25262b}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
h1{font-size:20px;margin:0 0 12px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
.card{background:var(--card);border-radius:10px;padding:10px 14px;min-width:120px}
.card b{display:block;font-size:20px}.card span{color:var(--mut);font-size:12px}
.banner{padding:10px 14px;border-radius:10px;margin-bottom:12px;background:#fde8e8;color:#8a1c1c}
.info{padding:10px 14px;border-radius:10px;margin-bottom:12px;background:#e6f2ff;color:#14487a}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.tabs a{padding:5px 12px;border-radius:16px;background:var(--card);color:var(--fg);text-decoration:none}
.tabs a.on{background:#2563eb;color:#fff}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:12px}
.small{color:var(--mut);font-size:12px}
.badge{padding:2px 9px;border-radius:12px;font-size:12px;white-space:nowrap}
.green{background:#d8f3dc;color:#1b5e20}.red{background:#fde8e8;color:#8a1c1c}
.amber{background:#fff1cc;color:#7a5200}.blue{background:#e1ecff;color:#1d4ed8}
.grey{background:#ececec;color:#444}
img.pv,video.pv{max-width:150px;max-height:90px;border-radius:6px;display:block}
button{padding:4px 10px;border-radius:6px;border:1px solid var(--line);background:var(--card);
color:var(--fg);cursor:pointer;margin:0 4px 4px 0}
form{display:inline}
a{color:#2563eb}
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.filters select,.filters button{padding:5px 8px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--fg)}
.bulk{margin:8px 0}
button.danger{background:#fde8e8;color:#8a1c1c;border-color:#f3b4b4}
img.pv{cursor:zoom-in}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-top:10px}
.gc{background:var(--card);border-radius:10px;overflow:hidden}
.gc a.zoom{display:block;position:relative}
.dt{white-space:pre-line;position:absolute;left:6px;top:6px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;font-weight:600;padding:2px 8px;border-radius:4px;pointer-events:none}
.fv{position:absolute;right:6px;top:2px;color:#ffd24a;font-size:24px;line-height:1.2;text-shadow:0 0 3px #000;pointer-events:none}
.favf{display:inline-flex;align-items:center;gap:4px;white-space:nowrap;margin-right:8px}.favf input{width:auto;margin:0}
.gc img{width:100%;aspect-ratio:16/9;object-fit:cover;display:block;background:#000;cursor:zoom-in}
.gc .t{padding:8px 12px 0;color:#6e9a35;font-weight:700;font-size:15px}
.gc .d{padding:0 12px 10px;color:#6e9a35;font-size:13px}
.cnt{display:inline-block;background:#6e9a35;color:#fff;padding:8px 16px;border-radius:4px;font-size:14px;border:none;cursor:pointer;margin:0}
button.cnt:hover{background:#7fae3f}button.cnt:disabled{opacity:.5;cursor:default}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between}
.sticky{position:sticky;top:0;z-index:5;background:var(--bg);padding:6px 0}
#lb{display:none;position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.88);
align-items:center;justify-content:center;flex-direction:column;color:#fff}
#lb .stage{display:flex;align-items:center;justify-content:center;width:100%;flex:none;height:min(56.25vw,calc(100vh - 250px))}
#lb{-webkit-user-select:none;user-select:none}
#lb .stage,#lb img{touch-action:none}
#lbb{position:fixed;display:none;pointer-events:none;z-index:1}
#lbbx{background:none;border:1px solid #fff;color:#fff;border-radius:4px;padding:3px 10px;cursor:pointer;margin:0 8px 0 0;font-size:13px}
#lbd .lo{color:#c4c4c4;font-weight:500}.lo{color:#8a8a8a;font-weight:500}
#lbd{white-space:pre-line;position:fixed;display:none;background:rgba(0,0,0,.65);color:#fff;font-size:14px;font-weight:600;padding:3px 10px;border-radius:4px;pointer-events:none;z-index:2}
#lb img{max-width:calc(100vw - 150px);max-height:100%;border-radius:6px;
transform-origin:center center;-webkit-touch-callout:none;-webkit-user-drag:none}
#lb .nav{position:absolute;top:50%;transform:translateY(-50%);z-index:2;background:none;border:none;color:#fff;
font-size:44px;padding:10px 18px;cursor:pointer;opacity:.85;margin:0}
#lb #lbp{left:10px}#lb #lbn{right:10px}
#lb .nav:disabled{opacity:.15;cursor:default}
#lb .fvb{position:absolute;top:8px;left:16px;background:none;border:none;color:#fff;font-size:34px;cursor:pointer;margin:0;padding:0 6px;line-height:1.2;z-index:3}
#lb .snd{position:absolute;top:14px;left:70px;z-index:3;margin:0;font-size:13px;padding:5px 11px;background:rgba(0,0,0,.55);color:#fff;border:1px solid rgba(255,255,255,.35);border-radius:14px;max-width:calc(100vw - 160px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#lb .snd:disabled{opacity:.6}
#lb #lbrow button.on{background:#6e9a35}
.lrow{display:flex;gap:4px;padding:0 12px 10px;flex-wrap:wrap}
.lrow button{font-size:12px;padding:3px 9px;border-radius:12px;border:1px solid var(--line,#8886);background:transparent;color:var(--fg);cursor:pointer;margin:0}
.lrow button.on{background:#6e9a35;color:#fff;border-color:#6e9a35}
.okm{color:#4fa65b;font-weight:700}
.mname{font-size:18px;font-weight:700;color:#6e9a35}
#lb .x{position:absolute;top:8px;right:16px;background:none;border:none;color:#fff;font-size:34px;
cursor:pointer;margin:0}
@media(max-width:700px){
#lb .nav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(0,0,0,.4);
border-radius:50%;width:44px;height:44px;padding:0;font-size:30px;line-height:40px;z-index:2}
#lb #lbp{left:6px}#lb #lbn{right:6px}
#lb img{max-width:100vw;max-height:100%;border-radius:0}}
#lb .ctl{display:flex;gap:10px;align-items:center;justify-content:center;padding:6px 6px 0}
#lb .ctl button{background:rgba(255,255,255,.14);border:none;color:#fff;border-radius:18px;
padding:6px 16px;font-size:16px;cursor:pointer;margin:0}
#lb .ctl input[type=range]{width:min(220px,40vw);accent-color:#6e9a35}
#lb .ctl .lbl{font-size:13px;min-width:2.6em}
#lb .cap{padding:0 16px 6px;font-size:18px;font-weight:600;line-height:1.3;text-align:center;width:100%;box-sizing:border-box}
#lb .cap div:first-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#lb .cap .ct{color:#4fa65b;font-size:28px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
@media(max-width:700px){#lbd{font-size:12px}#lb .ctl{gap:6px;padding:6px 4px 0}#lb .ctl button{padding:6px 12px}#lb .ctl input[type=range]{width:24vw}#lb .ctl .lbl{min-width:0;font-size:12px}#lb .cap{font-size:14px;padding:0 10px 6px}#lb .cap .ct{font-size:24px}}
"""


def fmt_time(ts):
    return time.strftime("%d.%m. %H:%M:%S", time.localtime(ts)) if ts else "-"


def fmt_size(num):
    if num >= 1024 * 1024:
        return "%.1f MB" % (num / 1024 / 1024)
    return "%.0f kB" % (num / 1024)


def fmt_dur(seconds):
    seconds = int(max(0, seconds))
    if seconds < 90:
        return "%d Sek" % seconds
    if seconds < 5400:
        return "%d Min" % round(seconds / 60)
    return "%.1f Std" % (seconds / 3600)


def e(value):
    return html.escape(str(value if value is not None else ""), quote=True)


TIME_CHOICES = [
    ("", "Alle Bilder"), ("heute", "Heute"), ("gestern", "Gestern"), ("woche", "Diese Woche"),
    ("monat", "Dieser Monat"), ("quartal", "Dieses Quartal"), ("jahr", "Dieses Jahr"),
    ("7t", "Letzte 7 Tage"), ("30t", "Letzte 30 Tage"), ("365t", "Letzte 365 Tage"),
    ("12m", "Letzte 12 Monate"),
]


PICKER_HTML = (
    '<div class="dp" id="dp" data-z="@Z@" data-von="@VON@" data-bis="@BIS@" data-all="@ALL@">'
    '<button type="button" class="dpic" id="dpopen" title="Zeitraum w&auml;hlen" aria-label="Zeitraum w&auml;hlen">'
    '<svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M19 3h-1V1h-2v2H8V1H6v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 '
    '2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 16H5V9h14zM7 11h5v5H7z"/></svg></button>'
    '<button type="button" class="dplabel" id="dplabel"></button>'
    '<button type="button" class="dpnow" id="dpnow">Jetzt</button>'
    '<button type="button" class="dparr" id="dpprev" title="Zur&uuml;ck" aria-label="Zur&uuml;ck">&#8249;</button>'
    '<button type="button" class="dparr" id="dpnext" title="Weiter" aria-label="Weiter">&#8250;</button>'
    '</div>'
    '<input type="hidden" name="z" value="@Z@"><input type="hidden" name="von" value="@VON@">'
    '<input type="hidden" name="bis" value="@BIS@">'
    '<dialog id="dpd"><div class="dpbox"><div class="dpmain"><div class="dppre">@PRESETS@</div>'
    '<div class="dpcalw"><div class="dphead"><button type="button" id="dpmp" aria-label="Vorheriger Monat">&#8249;</button>'
    '<span id="dpm"></span><button type="button" id="dpmn" aria-label="N&auml;chster Monat">&#8250;</button></div>'
    '<div class="dpgrid" id="dpg"></div>'
    '<div class="dptime">Uhrzeit von <input type="time" id="dpt1" value="00:00"> bis '
    '<input type="time" id="dpt2" value="23:59"></div></div></div>'
    '<div class="dpfoot"><button type="button" id="dpcancel">Abbrechen</button>'
    '<button type="button" class="dpprim" id="dpok">Ausw&auml;hlen</button></div></div></dialog>'
)

PICKER_JS = r"""
(function(){
var bar=document.getElementById('dp');if(!bar)return;
var f=bar.closest('form'),hz=f.elements.z,hv=f.elements.von,hb=f.elements.bis,dlg=document.getElementById('dpd');
var MON=['Jan.','Feb.','M\u00e4r.','Apr.','Mai','Jun.','Jul.','Aug.','Sep.','Okt.','Nov.','Dez.'];
var FULL=['Januar','Februar','M\u00e4rz','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
var today=new Date();
function z2(n){return (n<10?'0':'')+n}
function pd(s){var m=/^(\d{4})-(\d\d)-(\d\d)(?:[T ](\d\d):(\d\d))?/.exec(s||'');
 return m?new Date(+m[1],m[2]-1,+m[3],+(m[4]||0),+(m[5]||0)):null}
function iso(d,t){return d.getFullYear()+'-'+z2(d.getMonth()+1)+'-'+z2(d.getDate())+'T'+t}
function hm(d){return z2(d.getHours())+':'+z2(d.getMinutes())}
function day(d){return new Date(d.getFullYear(),d.getMonth(),d.getDate())}
function same(a,b){return a&&b&&a.getFullYear()==b.getFullYear()&&a.getMonth()==b.getMonth()&&a.getDate()==b.getDate()}
var S=pd(bar.dataset.von),E=pd(bar.dataset.bis);
function fd(d,t){return d.getDate()+'. '+MON[d.getMonth()]+(d.getFullYear()!=today.getFullYear()?' '+d.getFullYear():'')+(t?' '+hm(d):'')}
function label(){
 if(!S&&!E)return bar.dataset.all||'Alle Bilder';
 var t=(S&&(S.getHours()||S.getMinutes()))||(E&&!(E.getHours()==23&&E.getMinutes()==59));
 if(S&&E){if(same(S,E))return t?fd(S)+' '+hm(S)+' \u2013 '+hm(E):fd(S);return fd(S,t)+' \u2013 '+fd(E,t)}
 return S?'ab '+fd(S,t):'bis '+fd(E,t)}
document.getElementById('dplabel').textContent=label();
function go(z,von,bis){hz.value=z;hv.value=von||'';hb.value=bis||'';f.submit()}
function monthRange(){if(!S||!E)return 0;var a=day(S),b=day(E);
 if(a.getDate()!=1||new Date(b.getFullYear(),b.getMonth()+1,0).getDate()!=b.getDate())return 0;
 return (b.getFullYear()-a.getFullYear())*12+b.getMonth()-a.getMonth()+1}
function shift(dir){if(!S||!E)return;var a=day(S),b=day(E),n=monthRange(),ns,ne;
 if(n){ns=new Date(a.getFullYear(),a.getMonth()+dir*n,1);ne=new Date(ns.getFullYear(),ns.getMonth()+n,0)}
 else{var d=Math.round((b-a)/864e5)+1;ns=new Date(a.getFullYear(),a.getMonth(),a.getDate()+dir*d);
  ne=new Date(b.getFullYear(),b.getMonth(),b.getDate()+dir*d)}
 go('x',iso(ns,hm(S)),iso(ne,hm(E)))}
function now(){var n=monthRange(),z='heute';
 if(S&&E){var a=day(S),b=day(E),d=Math.round((b-a)/864e5)+1;
  if(n==1)z='monat';else if(n==3&&a.getMonth()%3==0)z='quartal';else if(n==12&&a.getMonth()==0)z='jahr';
  else if(d==7&&((a.getDay()+6)%7)==0)z='woche'}
 go(z)}
document.getElementById('dpprev').onclick=function(){shift(-1)};
document.getElementById('dpnext').onclick=function(){shift(1)};
document.getElementById('dpnow').onclick=now;
if(!S||!E){document.getElementById('dpprev').disabled=true;document.getElementById('dpnext').disabled=true}
var vy,vm,ss,ee;
function render(){
 document.getElementById('dpm').textContent=FULL[vm]+' '+vy;
 var g=document.getElementById('dpg'),h='';
 'MDMDFSS'.split('').forEach(function(c){h+='<b>'+c+'</b>'});
 var first=new Date(vy,vm,1),off=(first.getDay()+6)%7,dim=new Date(vy,vm+1,0).getDate(),cells=Math.ceil((off+dim)/7)*7;
 for(var i=0;i<cells;i++){var d=new Date(vy,vm,1-off+i),c='d';
  if(d.getMonth()!=vm)c+=' o';if(same(d,today))c+=' t';
  var lo=ss,hi=ee||ss;
  if(lo&&same(d,lo))c+=' a';if(hi&&same(d,hi))c+=' z';
  if(lo&&hi&&d>lo&&d<hi)c+=' r';
  h+='<button type="button" class="'+c+'" data-d="'+iso(d,'00:00')+'">'+d.getDate()+'</button>'}
 g.innerHTML=h}
document.getElementById('dpg').onclick=function(ev){var b=ev.target.closest('button[data-d]');if(!b)return;
 var d=day(pd(b.dataset.d));
 if(!ss||ee){ss=d;ee=null}else if(d<ss){ee=ss;ss=d}else ee=d;
 if(d.getMonth()!=vm){vy=d.getFullYear();vm=d.getMonth()}
 render()};
document.getElementById('dpmp').onclick=function(){vm--;if(vm<0){vm=11;vy--}render()};
document.getElementById('dpmn').onclick=function(){vm++;if(vm>11){vm=0;vy++}render()};
document.getElementById('dpopen').onclick=document.getElementById('dplabel').onclick=function(){
 ss=S?day(S):null;ee=E&&S&&!same(S,E)?day(E):null;
 var base=S||E||today;vy=base.getFullYear();vm=base.getMonth();
 document.getElementById('dpt1').value=S?hm(S):'00:00';document.getElementById('dpt2').value=E?hm(E):'23:59';
 render();dlg.showModal()};
document.getElementById('dpcancel').onclick=function(){dlg.close()};
dlg.addEventListener('click',function(ev){if(ev.target===dlg)dlg.close()});
dlg.querySelector('.dppre').onclick=function(ev){var b=ev.target.closest('button[data-z]');if(b)go(b.dataset.z)};
document.getElementById('dpok').onclick=function(){
 if(!ss){dlg.close();return}
 var a=ss,b=ee||ss,t1=document.getElementById('dpt1').value||'00:00',t2=document.getElementById('dpt2').value||'23:59';
 if(same(a,b)&&t1>t2){var x=t1;t1=t2;t2=x}
 go('x',iso(a,t1),iso(b,t2))};
})();
"""

PICKER_CSS = """
.dp{display:flex;align-items:center;gap:6px;flex:1 1 100%;margin:0 0 4px}
.dp button{margin:0;border:0;background:transparent;color:var(--fg);cursor:pointer;font:inherit}
.dp .dpic{padding:6px;display:flex}
.dp .dplabel{flex:1;min-width:0;text-align:left;padding:10px 14px;border-radius:10px;background:var(--card);
font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(max-width:520px){.dp .dplabel{font-size:15px;padding:10px}.dp .dparr{padding:4px 6px}}
.dp .dpnow{padding:8px 16px;border-radius:20px;background:rgba(37,99,235,.18);color:#3b82f6;font-weight:600}
.dp .dparr{font-size:26px;line-height:1;padding:4px 12px}
.dp button:disabled{opacity:.3;cursor:default}
dialog#dpd{border:1px solid var(--line);border-radius:14px;background:var(--bg);color:var(--fg);padding:0;
max-width:min(560px,96vw);width:560px}
dialog#dpd::backdrop{background:rgba(0,0,0,.5)}
.dpmain{display:flex}
.dppre{display:flex;flex-direction:column;border-right:1px solid var(--line);padding:6px 0;min-width:150px}
.dppre button{text-align:left;padding:9px 18px;margin:0;border:0;border-radius:0;background:transparent;font-size:15px}
.dppre button.on{font-weight:700;color:#2563eb}
.dpcalw{flex:1;padding:10px 12px}
.dphead{display:flex;align-items:center;justify-content:space-between;font-weight:600;margin-bottom:6px}
.dphead button{margin:0;border:0;background:transparent;font-size:24px;padding:0 12px;color:var(--fg)}
.dpgrid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px;text-align:center}
.dpgrid b{color:var(--mut);font-size:12px;padding:6px 0}
.dpgrid .d{margin:0;border:0;background:transparent;color:var(--fg);aspect-ratio:1;border-radius:50%;font-size:14px;padding:0}
.dpgrid .d.o{color:var(--mut);opacity:.5}
.dpgrid .d.t{box-shadow:inset 0 0 0 1px var(--mut)}
.dpgrid .d.r{background:rgba(37,99,235,.2);border-radius:0}
.dpgrid .d.a,.dpgrid .d.z{background:#2563eb;color:#fff;opacity:1}
.dptime{margin-top:10px;color:var(--mut);font-size:13px}
.dptime input{padding:3px 6px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.dpfoot{display:flex;justify-content:flex-end;gap:8px;padding:10px 14px;border-top:1px solid var(--line)}
.dpfoot button{margin:0;padding:8px 18px;border-radius:20px;background:transparent;border:0;color:#2563eb;font-size:14px}
.dpfoot .dpprim{background:#2563eb;color:#fff}
@media(max-width:520px){.dpmain{flex-direction:column}.dppre{flex-direction:row;overflow-x:auto;border-right:0;
border-bottom:1px solid var(--line);padding:0}.dppre button{white-space:nowrap;padding:9px 12px}}
"""

DET_BADGES = {
    "tier": ("Tier", "green"),
    "mensch": ("Mensch", "amber"),
    "fahrzeug": ("Fahrzeug", "blue"),
    "leer": ("leer", "grey"),
    "fehler": ("?", "grey"),
}
DET_OPTIONS = (("", "alle Fotos"), ("erkannt", "irgendeine Erkennung"), ("tier", "mit Tier"),
               ("mensch", "mit Mensch"), ("fahrzeug", "mit Fahrzeug"), ("leer", "leer (Fehlalarm?)"),
               ("offen", "noch nicht gepr\u00fcft"))


def sim_ok(row, pre="det"):
    """Gibt es bei diesem Favoriten gemerkte Werte (auch unter der Schwelle) zum aktuellen Modell? Dann laesst sich mit
    beliebigen Schwellen nachrechnen, was gemeldet worden waere."""
    keys = row.keys()
    if "fav" not in keys or not row["fav"] or pre + "_low" not in keys or not row[pre + "_low"]:
        return False
    if pre == "test":
        return test_active(row)
    return "det_model" in keys and row["det_model"] == _detect.MODEL_TAG


def sim_items(row, pre="det"):
    """[(Etikett, Sicherheit)] bei den *aktuell eingestellten* Schwellen des Modells (aus den gemerkten Werten)."""
    th = _detect.thresholds_for(_detect.MODEL_TAG if pre == "det" else _detect.TEST_TAG)
    out = []
    for part in row[pre + "_low"].split(","):
        name, _, val = part.partition(":")
        try:
            c = float(val)
        except ValueError:
            continue
        if name in th and round(c * 100) >= round(th[name] * 100):
            out.append((name, c))
    return sorted(out, key=lambda x: _detect.ORDER.index(x[0]))


def det_items(row, pre="det", sim=False):
    """[(Etikett, Sicherheit)] in Anzeige-Reihenfolge; leer, wenn nichts erkannt oder noch nicht geprueft.
    sim=True: bei Favoriten mit gemerkten Werten nach den aktuell eingestellten Schwellen statt nach dem damaligen Lauf."""
    if sim and sim_ok(row, pre):
        return sim_items(row, pre)
    keys = row.keys()
    label = row[pre + "_label"] if pre + "_label" in keys else ""
    if not label or label in ("leer", "fehler"):
        return []
    confs = {}
    raw = row[pre + "_confs"] if pre + "_confs" in keys else ""
    for part in (raw or "").split(","):
        name, _, val = part.partition(":")
        try:
            confs[name] = float(val)
        except ValueError:
            pass
    names = [n for n in label.split(",") if n in DET_BADGES]
    single = row["det_conf"] if pre == "det" and "det_conf" in keys else 0
    return [(n, confs.get(n, single if len(names) == 1 else 0)) for n in names]


def test_active(row):
    """Testmodell-Ergebnis nur bei Favoriten und nur, solange es zur aktuellen test.onnx passt."""
    keys = row.keys()
    return bool(_detect.TEST_TAG and "fav" in keys and row["fav"] and "test_model" in keys
                and row["test_model"] == _detect.TEST_TAG and row["test_label"])


def test_text(row, sim=False):
    if not test_active(row):
        return ""
    items = det_items(row, "test", sim)
    if items:
        return "\n".join("%s %d %%" % (DET_BADGES[n][0], round(c * 100)) if c else DET_BADGES[n][0] for n, c in items)
    return "Nichts erkannt" if (row["test_label"] == "leer" or (sim and sim_ok(row, "test"))) else "nicht auswertbar"


def _boxes_for(row, pre, sim):
    """Rahmen der gemeldeten Klassen. Bei sim: nur Klassen, die bei den aktuellen Schwellen gemeldet wuerden; fehlt der Rahmen
    aus dem damaligen Lauf (Klasse lag damals unter der Schwelle), kommt der gemerkte beste Rahmen."""
    keys = row.keys()
    raw = (row[pre + "_boxes"] if pre + "_boxes" in keys else "") or ""
    if not (sim and sim_ok(row, pre)):
        return raw
    names = {n for n, _c in sim_items(row, pre)}
    parts = [p for p in raw.split(";") if p and p.split(":")[0] in names]
    have = {p.split(":")[0] for p in parts}
    for p in (row[pre + "_lowb"] or "").split(";"):
        if p and p.split(":")[0] in names and p.split(":")[0] not in have:
            parts.append(p)
    return ";".join(parts)


def test_boxes(row, sim=False):
    return _boxes_for(row, "test", sim) if test_active(row) else ""


def low_items(row, pre="det", sim=False):
    """[(Name, Sicherheit)] der Klassen, die ein Modell bei diesem Favoriten nur unter der Schwelle gesehen hat (sonst leer)."""
    keys = row.keys()
    if "fav" not in keys or not row["fav"] or pre + "_low" not in keys or not row[pre + "_low"]:
        return []
    if pre == "test" and not test_active(row):
        return []
    have = {n for n, _c in det_items(row, pre, sim)}
    out = []
    for part in row[pre + "_low"].split(","):
        name, _, val = part.partition(":")
        try:
            if name in DET_BADGES and name not in have:
                out.append((name, float(val)))
        except ValueError:
            pass
    return sorted(out, key=lambda x: _detect.ORDER.index(x[0]) if x[0] in _detect.ORDER else 9)


def low_text(row, pre="det", sim=False):
    """\"Tier 18 %, Mensch 12 %\" fuer die Klassen unter der Schwelle (leer, wenn keine)."""
    return ", ".join("%s %d %%" % (DET_BADGES[n][0], round(c * 100)) for n, c in low_items(row, pre, sim))


def low_boxes(row, pre="det", sim=False):
    """Rahmen der Erkennungen unter der Schwelle fuers data-Attribut (nur Klassen, die nicht gemeldet wurden)."""
    names = {n for n, _c in low_items(row, pre, sim)}
    if not names:
        return ""
    raw = row[pre + "_lowb"] if pre + "_lowb" in row.keys() else ""
    return ";".join(p for p in (raw or "").split(";") if p.split(":")[0] in names)


def low_popup(row, sim=False):
    """Text fuer die grauen Zeilen im grossen Bild."""
    a, b = low_text(row, "det", sim), low_text(row, "test", sim)
    if a and b:
        return "Bisher unter Schwelle: %s\nTest unter Schwelle: %s" % (a, b)
    if a:
        return ("Bisher " if test_active(row) else "") + "unter Schwelle: " + a
    if b:
        return "Test unter Schwelle: " + b
    return ""


def zoom_attrs(row, caption, sim=False):
    """data-Attribute eines Foto-Links fuers grosse Bild (Beschriftung, Erkennung, Stern, Testmodell)."""
    keys = row.keys()
    st = STATUS.get(row["status"], (row["status"], ""))[0] if "status" in keys else ""
    return ('data-cap="%s" data-det="%s" data-box="%s" data-id="%d" data-fav="%d" data-test="%s" data-tbox="%s"'
            ' data-st="%s" data-sk="%s" data-lbl="%s" data-low="%s" data-lbox="%s" data-tlbox="%s"'
            % (e(caption), e(det_text(row, sim)), e(det_boxes(row, sim)), row["id"],
               1 if "fav" in keys and row["fav"] else 0, e(test_text(row, sim)), e(test_boxes(row, sim)),
               e(st), e(row["status"] if "status" in keys else ""), e(row["lbl"] if "lbl" in keys and row["lbl"] else ""),
               e(low_popup(row, sim)), e(low_boxes(row, "det", sim)), e(low_boxes(row, "test", sim))))


def det_boxes(row, sim=False):
    """Rahmen fuers Vorschaubild als Text fuers data-Attribut (leer, wenn es keine gibt)."""
    return _boxes_for(row, "det", sim)


def det_text(row, sim=False):
    """Kurztext fuer die Einblendung links oben im Bild."""
    items = det_items(row, "det", sim)
    if items:
        return "\n".join("%s %d %%" % (DET_BADGES[n][0], round(c * 100)) if c else DET_BADGES[n][0]
                         for n, c in items)
    label = row["det_label"] if "det_label" in row.keys() else ""
    return "Nichts erkannt" if (label == "leer" or (sim and sim_ok(row, "det"))) else ""


def status_badge(row):
    """Kleines Etikett im Foto-Raster, solange das Foto nicht weitergeleitet wurde (zugestellt = kein Etikett)."""
    status = row["status"] if "status" in row.keys() else "sent"
    if status in ("sent", "queued", "sending", "held"):
        return ""
    label, color = STATUS.get(status, (status, "grey"))
    return ' <span class="badge %s">%s</span>' % (color, e(label))


def det_badge(row):
    """Kleine Etiketten mit dem Ergebnis der Bilderkennung (leer, solange die Mail nicht geprueft ist)."""
    items = det_items(row)
    if not items:
        label = row["det_label"] if "det_label" in row.keys() else ""
        if label in ("leer", "fehler"):
            text, color = DET_BADGES[label]
            return ' <span class="badge %s" title="Bilderkennung: %s">%s</span>' % (
                color, e("nichts erkannt" if label == "leer" else "nicht auswertbar"), e(text))
        return ""
    out = ""
    for n, c in items:
        text, color = DET_BADGES[n]
        tip = "Bilderkennung: %s%s" % (text, " (%d %%)" % round(c * 100) if c else "")
        out += ' <span class="badge %s" title="%s">%s</span>' % (color, e(tip), e(text))
    return out


class Board:
    def __init__(self, cfg, store, runtime, wake, bind, port, allowed, detector=None):
        self.detector = detector
        self.cfg = cfg
        self.store = store
        self.runtime = runtime
        self.wake = wake
        self.bind = bind
        self.port = port
        self.allowed = set(allowed)
        self.server = None

    # -- Server ---------------------------------------------------------

    def start(self):
        board = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "KameraBoard"

            def log_message(self, fmt, *args):  # kein Zugriffsprotokoll
                pass

            def _allowed(self):
                if self.client_address[0] in board.allowed:
                    return True
                log.warning("Board: Anfrage von %s abgelehnt", self.client_address[0])
                self.send_error(403)
                return False

            def do_GET(self):
                if self._allowed():
                    board.handle(self, "GET")

            def do_POST(self):
                if self._allowed():
                    board.handle(self, "POST")

        try:
            self.server = ThreadingHTTPServer((self.bind, self.port), Handler)
        except OSError as exc:
            log.error("Board kann nicht gestartet werden (%s:%s): %s", self.bind, self.port, exc)
            return False
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="board", daemon=True).start()
        log.info("Board laeuft auf %s:%s (Seitenleiste von Home Assistant)", self.bind, self.port)
        return True

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    # -- Anfragen -------------------------------------------------------

    @staticmethod
    def _ingress(req):
        path = req.headers.get("X-Ingress-Path", "")
        return path if re.fullmatch(r"/[A-Za-z0-9/_.\-]*", path or "") else ""

    @staticmethod
    def _send(req, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        req.send_response(code)
        req.send_header("Content-Type", ctype)
        req.send_header("Content-Length", str(len(data)))
        req.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            req.send_header(key, value)
        req.end_headers()
        if req.command != "HEAD":
            req.wfile.write(data)

    def handle(self, req, method):
        url = urlparse(req.path)
        path = re.sub(r"/{2,}", "/", url.path)
        query = parse_qs(url.query, keep_blank_values=True)
        try:
            if method == "POST":
                if path == "/api/model-upload":
                    return self._model_upload(req)
                if path == "/api/model-upload-chunk":
                    return self._model_upload_chunk(req)
                return self._post(req, path)
            if path == "/api/model-upload-state":
                return self._model_upload_state(req, query)
            if path in ("/", "/index.html"):
                return self._gallery_page(req, query)
            if path == "/mails":
                return self._page(req, query)
            if path == "/auswertung":
                return self._eval_page(req, query)
            if path == "/models":
                return self._send(
                    req, 200,
                    self.render_models(self._ingress(req) + "/", (query.get("m") or [""])[0][:200],
                                       (query.get("d") or [""])[0] == "1", (query.get("frag") or [""])[0],
                                       self._flt(query)),
                    extra={"Cache-Control": "no-store"},
                )
            if path == "/reviere":
                return self._send(
                    req, 200,
                    self.render_reviere(self._ingress(req) + "/", (query.get("m") or [""])[0]),
                    extra={"Cache-Control": "no-store"},
                )
            if path == "/cameras":
                return self._send(
                    req, 200,
                    self.render_cameras(self._ingress(req) + "/", (query.get("m") or [""])[0]),
                    extra={"Cache-Control": "no-store"},
                )
            match = re.fullmatch(r"/mail/(\d+)/eml", path)
            if match:
                return self._eml(req, int(match.group(1)))
            match = re.fullmatch(r"/mail/(\d+)/download/(\d+)", path)
            if match:
                return self._download(req, int(match.group(1)), int(match.group(2)))
            match = re.fullmatch(r"/mail/(\d+)/preview", path)
            if match:
                return self._preview(req, int(match.group(1)))
            match = re.fullmatch(r"/mail/(\d+)/part/(\d+)", path)
            if match:
                return self._part(req, int(match.group(1)), int(match.group(2)))
            self._send(req, 404, "Nicht gefunden", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            log.error("Board: Fehler bei %s: %r", path, exc)
            try:
                self._send(req, 500, "Interner Fehler", "text/plain; charset=utf-8")
            except OSError:
                pass

    @staticmethod
    def _ctx(source):
        """Status-Reiter, Revier und Kamera aus Formular oder Adresszeile."""
        flt = (source.get("f") or [(source.get("st") or ["open"])[0] or "open"])[0]
        return {
            "f": flt if flt in dict(TABS) else "open",
            "r": (source.get("r") or [""])[0][:80],
            "c": (source.get("c") or [""])[0][:200],
            "z": (source.get("z") or [""])[0][:40],
            "von": (source.get("von") or [""])[0][:40],
            "bis": (source.get("bis") or [""])[0][:40],
        }

    def _range(self, ctx):
        """(Voreinstellung, Start, Ende, since, until) der gemeinsamen Zeitauswahl."""
        preset, start, end = self.time_range(ctx.get("z", ""), ctx.get("von", ""), ctx.get("bis", ""))
        since = time.mktime(start.timetuple()) if start else None
        until = time.mktime((end + timedelta(minutes=1)).timetuple()) if end else None
        return preset, start, end, since, until

    @staticmethod
    def _qs(ctx, **extra):
        items = [("f", ctx["f"])] + [(k, ctx.get(k, "")) for k in ("r", "c", "z", "von", "bis") if ctx.get(k)]
        items += [(k, v) for k, v in extra.items() if v]
        return "mails?" + "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in items)

    @staticmethod
    def _hidden(ctx):
        return "".join(
            '<input type="hidden" name="%s" value="%s">' % (k, e(ctx[k]))
            for k in ("f", "r", "c", "z", "von", "bis")
            if ctx.get(k) or k == "f"
        )

    @staticmethod
    def _cam_title(revier, name):
        """'Revier Name'; sind beide gleich (z. B. Hof/Hof), steht es nur einmal da."""
        revier, name = (revier or "").strip(), (name or "").strip()
        if revier.casefold() == name.casefold():
            return name
        return " ".join(t for t in (revier, name) if t)

    @staticmethod
    def _revier_of(row):
        if not row["name"] and not row["revier"]:
            return "Unbenannt"
        return row["revier"] or "Ohne Revier"

    def _addresses(self, ctx):
        """Adressen, auf die der Filter passt (None = keine Einschraenkung)."""
        if ctx["c"]:
            return [ctx["c"].lower()]
        if ctx["r"]:
            return [x["address"] for x in self.store.cameras() if self._revier_of(x) == ctx["r"]]
        return None

    def _redirect(self, req, ctx, message=""):
        base = self._ingress(req) + "/"
        target = base + self._qs(ctx, m=message)
        req.send_response(303)
        req.send_header("Location", target)
        req.send_header("Content-Length", "0")
        req.end_headers()

    def _post(self, req, path):
        limit = 262144 if path == "/api/cameras" else 4096
        length = min(int(req.headers.get("Content-Length") or 0), limit)
        form = parse_qs(req.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
        if path == "/api/cameras":
            addresses = form.get("addr", [])
            names = form.get("name", [])
            reviers = form.get("revier", [])
            saved, errors = 0, []
            for i, address in enumerate(addresses):
                name = names[i] if i < len(names) else ""
                revier = reviers[i] if i < len(reviers) else ""
                try:
                    if self.store.set_camera(address, name, revier):
                        saved += 1
                except CameraError as exc:
                    errors.append(str(exc))
            log.info("Kameratabelle im Board gespeichert (%d Eintraege)%s", saved,
                     ", %d abgelehnt: %s" % (len(errors), "; ".join(errors)) if errors else "")
            msg = "saved" if not errors else "e:" + " ".join(errors[:3]) + (" Die uebrigen Eintraege wurden gespeichert." if saved else "")
            req.send_response(303)
            req.send_header("Location", self._ingress(req) + "/cameras?m=" + quote(msg, safe=":"))
            req.send_header("Content-Length", "0")
            req.end_headers()
            return
        if path == "/api/reviere":
            action = (form.get("action") or [""])[0]
            name = (form.get("name") or [""])[0]
            old = (form.get("old") or [""])[0]
            try:
                if action == "add":
                    self.store.add_revier(name)
                    msg, note = "i:Revier %s angelegt." % name.strip(), "Revier angelegt: %s" % name.strip()
                elif action == "rename":
                    self.store.rename_revier(old, name)
                    msg, note = "i:Revier %s umbenannt in %s." % (old, name.strip()), "Revier umbenannt: %s -> %s" % (old, name.strip())
                elif action == "delete":
                    self.store.delete_revier(old)
                    msg, note = "i:Revier %s geloescht." % old, "Revier geloescht: %s" % old
                else:
                    msg, note = "", ""
                if note:
                    log.info("Board: %s", note)
            except CameraError as exc:
                msg = "e:" + str(exc)
            req.send_response(303)
            req.send_header("Location", self._ingress(req) + "/reviere?m=" + quote(msg, safe=":"))
            req.send_header("Content-Length", "0")
            req.end_headers()
            return
        match = re.fullmatch(r"/api/model-(promote|delete-test|rollback|builtin|recheck|recheck-cancel|recheck-fav|favtest|favtest-cancel|thresholds)", path)
        if match:
            message = self._model_action(match.group(1), form)
            req.send_response(303)
            req.send_header("Location", self._ingress(req) + "/models?m=" + quote(message, safe=""))
            req.send_header("Content-Length", "0")
            req.end_headers()
            return
        match = re.fullmatch(r"/api/fav/(\d+)", path)
        if match:
            mail_id = int(match.group(1))
            want = (form.get("v") or ["1"])[0] == "1"
            value = self.store.set_fav(mail_id, want)
            if value is None:
                return self._send(req, 404, "Nicht gefunden", "text/plain; charset=utf-8")
            log.info("[#%d] Favorit %s", mail_id, "gesetzt" if value else "entfernt")
            if value and self.detector is not None:
                self.detector.submit_test(mail_id)
            return self._send(req, 200, '{"fav": %d}' % value, "application/json", {"Cache-Control": "no-store"})
        match = re.fullmatch(r"/api/label/(\d+)", path)
        if match:
            mail_id = int(match.group(1))
            value = self.store.set_label(mail_id, (form.get("v") or [""])[0])
            if value is None:
                return self._send(req, 404, "Nicht gefunden", "text/plain; charset=utf-8")
            log.info("[#%d] Label gesetzt: %s", mail_id, value or "(entfernt)")
            return self._send(req, 200, json.dumps({"lbl": value}), "application/json", {"Cache-Control": "no-store"})
        match = re.fullmatch(r"/api/resend-photo/(\d+)", path)
        if match:
            mail_id = int(match.group(1))
            if self.store.requeue(mail_id):
                log.info("[#%d] Foto im Board erneut zur Zustellung eingereiht", mail_id)
                self.wake()
                return self._send(req, 200, '{"ok": 1}', "application/json", {"Cache-Control": "no-store"})
            return self._send(req, 409, '{"ok": 0}', "application/json", {"Cache-Control": "no-store"})
        ctx = self._ctx(form)
        if path == "/api/delete-bulk":
            if (form.get("mode") or [""])[0] == "all":
                _p, _s, _e, since, until = self._range(ctx)
                ids = self.store.matching_ids(ctx["f"], self._addresses(ctx), since=since, until=until)
            else:
                ids = [int(x) for x in form.get("ids", []) if x.isdigit()]
            done = self.store.delete_many(ids)
            log.info("Board: %d Mail(s) auf Wunsch geloescht (%s)", len(done),
                     "alle in dieser Ansicht" if (form.get("mode") or [""])[0] == "all" else "Auswahl")
            return self._redirect(req, ctx, "deleted%d" % len(done))
        match = re.fullmatch(r"/api/(resend|delete)/(\d+)", path)
        if match:
            action, mail_id = match.group(1), int(match.group(2))
            if action == "resend":
                if self.store.requeue(mail_id):
                    log.info("[#%d] auf Wunsch im Board erneut zur Zustellung eingereiht", mail_id)
                    self.wake()
                    return self._redirect(req, ctx, "resent")
                return self._redirect(req, ctx, "noresend")
            self.store.delete(mail_id)
            log.info("[#%d] im Board geloescht", mail_id)
            return self._redirect(req, ctx, "deleted")
        if path == "/api/resend-failed":
            ids = self.store.requeue_failed()
            for mail_id in ids:
                log.info("[#%d] auf Wunsch im Board erneut zur Zustellung eingereiht", mail_id)
            if ids:
                self.wake()
            return self._redirect(req, ctx, "resent%d" % len(ids))
        self._send(req, 404, "Nicht gefunden", "text/plain; charset=utf-8")

    def _eml(self, req, mail_id):
        data = self.store.read(mail_id)
        if data is None:
            return self._send(req, 404, "Mail nicht mehr gespeichert", "text/plain; charset=utf-8")
        self._send(
            req,
            200,
            data,
            "message/rfc822",
            {"Content-Disposition": 'attachment; filename="kamera-mail-%d.eml"' % mail_id},
        )

    def _download(self, req, mail_id, number):
        """Liefert einen Anhang (Foto oder Video) als Datei; number zaehlt ab 1."""
        data = self.store.read(mail_id)
        if data is None:
            photo = self.store.preview_path(mail_id) if number == 1 else None
            if photo:   # Mail-Datei gibt es nicht mehr, das aufbewahrte Foto schon
                ext = photo.rsplit(".", 1)[-1]
                try:
                    with open(photo, "rb") as fh:
                        payload = fh.read()
                except OSError:
                    payload = None
                if payload is not None:
                    return self._send(req, 200, payload, PREVIEW_TYPE.get(ext, "application/octet-stream"), {
                        "Content-Disposition": 'attachment; filename="foto-%d.%s"' % (mail_id, ext),
                        "Content-Security-Policy": "default-src 'none'; sandbox",
                    })
            return self._send(req, 404, "Mail nicht mehr gespeichert", "text/plain; charset=utf-8")
        msg = BytesParser(policy=email.policy.default).parsebytes(data)
        found = 0
        for part in msg.walk():
            ctype = part.get_content_type()
            if not (ctype.startswith("image/") or ctype.startswith("video/")):
                continue
            found += 1
            if found != number:
                continue
            payload = part.get_payload(decode=True) or b""
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", part.get_filename() or "").strip("._")
            if not name:
                name = "kamera-mail-%d-%d%s" % (
                    mail_id,
                    number,
                    mimetypes.guess_extension(ctype) or ".bin",
                )
            return self._send(
                req,
                200,
                payload,
                ctype if ctype in SHOWN_TYPES else "application/octet-stream",
                {
                    "Content-Disposition": 'attachment; filename="%s"' % name,
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                },
            )
        self._send(req, 404, "Anhang nicht gefunden", "text/plain; charset=utf-8")

    def _preview(self, req, mail_id):
        path = self.store.preview_path(mail_id)
        if path is None:
            return self._send(req, 404, "Vorschau nicht vorhanden", "text/plain; charset=utf-8")
        with open(path, "rb") as fh:
            data = fh.read()
        self._send(
            req, 200, data, PREVIEW_TYPE.get(path.rsplit(".", 1)[-1], "image/jpeg"),
            {"Cache-Control": "private, max-age=3600",
             "Content-Security-Policy": "default-src 'none'; sandbox"},
        )

    def _part(self, req, mail_id, index):
        data = self.store.read(mail_id)
        if data is None:
            return self._send(req, 404, "Mail nicht mehr gespeichert", "text/plain; charset=utf-8")
        msg = BytesParser(policy=email.policy.default).parsebytes(data)
        for i, part in enumerate(msg.walk()):
            if i != index:
                continue
            ctype = part.get_content_type()
            if ctype not in SHOWN_TYPES:
                break
            payload = part.get_payload(decode=True) or b""
            headers = {
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=300",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            }
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", req.headers.get("Range", "") or "")
            if rng and payload and (rng.group(1) or rng.group(2)):
                size = len(payload)
                if rng.group(1):
                    start = int(rng.group(1))
                    end = int(rng.group(2)) if rng.group(2) else size - 1
                else:
                    start = max(0, size - int(rng.group(2)))
                    end = size - 1
                end = min(end, size - 1)
                if start <= end < size:
                    headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
                    return self._send(req, 206, payload[start : end + 1], ctype, headers)
            return self._send(req, 200, payload, ctype, headers)
        self._send(req, 404, "Teil nicht gefunden", "text/plain; charset=utf-8")

    # -- Seite ----------------------------------------------------------

    def _page(self, req, query):
        ctx = self._ctx(query)
        base = self._ingress(req) + "/"
        before = (query.get("b") or [""])[0]
        na = (query.get("na") or [""])[0]
        if na.isdigit():
            _p, _s, _e, since, until = self._range(ctx)
            rows = self.store.list_rows(ctx["f"], 100, self._addresses(ctx), since=since, until=until)
            return self._send(req, 200, json.dumps({"n": sum(1 for r in rows if r["id"] > int(na))}),
                              "application/json", {"Cache-Control": "no-store"})
        top = self.store.max_id()
        body = self.render(ctx["f"], base, (query.get("m") or [""])[0], ctx["r"], ctx["c"],
                           zeit=ctx["z"], von=ctx["von"], bis=ctx["bis"],
                           before=int(before) if before.isdigit() else None,
                           frag=(query.get("frag") or [""])[0] == "1")
        if (query.get("frag") or [""])[0] != "1":
            body = body.replace("</body>", '<i id="topid" data-t="%d" hidden></i></body>' % top, 1)
        self._send(req, 200, body, extra={"Cache-Control": "no-store"})

    def _message(self, code):
        if code == "resent":
            return "Mail wurde erneut zur Zustellung eingereiht."
        if code.startswith("resent") and code[6:].isdigit():
            return "%s Mail(s) erneut zur Zustellung eingereiht." % code[6:]
        if code == "noresend":
            return "Dieses Foto kann nicht erneut gesendet werden (nicht mehr gespeichert oder wartet bereits)."
        if code == "deleted":
            return "Mail wurde geloescht."
        if code.startswith("deleted") and code[7:].isdigit():
            return "%s Mail(s) geloescht." % code[7:]
        return ""

    def _storage_notice(self):
        """Hinweis nur, wenn der Speicher voll ist und die aeltesten Fotos automatisch geloescht werden."""
        info = self.store.storage_info()
        if not info["full"]:
            return ""
        return '<div class="banner">&#9888; %s</div>' % e(
            "Speicher voll (%s von %s): die \u00e4ltesten Fotos werden automatisch gel\u00f6scht, "
            "Favoriten bleiben. Speicherlimit oder Aufbewahrung in den Einstellungen anpassen."
            % (fmt_size(info["used"]), fmt_size(info["cap"])))

    LIST_PAGE = 500

    def render(self, flt, base, message="", revier="", camera="", zeit="", von="", bis="", before=None, frag=False):
        now = time.time()
        ctx = {"f": flt, "r": revier, "c": camera, "z": zeit, "von": von, "bis": bis}
        addresses = self._addresses(ctx)
        preset, start, end, since, until = self._range(ctx)
        counts = self.store.counts()
        view_counts = self.store.counts(addresses, since, until)
        rows = self.store.list_rows(flt, self.LIST_PAGE + 1, addresses, since=since, until=until, before_id=before)
        more = len(rows) > self.LIST_PAGE
        rows = rows[:self.LIST_PAGE]
        if frag:   # Nachladen: nur die Zeilen plus Marke
            cams_f = self.store.camera_map()
            return "".join(self._row(r, ctx, time.time(), cams_f) for r in rows) + (
                '<i id="fragmore" data-b="%d"></i>' % rows[-1]["id"] if more and rows else "")
        rt = self.runtime
        cfg = self.cfg

        waiting = self.store.oldest_waiting(now)
        notices = []
        if waiting is not None and waiting > 900:
            notices.append(
                '<div class="banner">&#9888; %s</div>'
                % e(
                    "Mails warten seit %s auf die Zustellung - ist das Ziel erreichbar? "
                    "Letzter Fehler: %s" % (fmt_dur(waiting), rt.last_error or "unbekannt")
                )
            )
        if rt.primary_skip_until > now:
            notices.append(
                '<div class="info">%s</div>'
                % e(
                    "Hauptserver nicht erreichbar - neue Mails gehen bis %s direkt ueber den "
                    "Backup-Server (%s). Letzter Fehler: %s"
                    % (
                        time.strftime("%H:%M:%S", time.localtime(rt.primary_skip_until)),
                        cfg["backup_host"],
                        rt.last_error or "unbekannt",
                    )
                )
            )
        if rt.pause_until > now:
            notices.append(
                '<div class="info">%s</div>'
                % e(
                    "Zustellung pausiert bis %s (Ziel nicht erreichbar)."
                    % time.strftime("%H:%M:%S", time.localtime(rt.pause_until))
                )
            )
        notices.append(self._storage_notice())
        text = self._message(message)
        if text:
            notices.append('<div class="info">%s</div>' % e(text))

        cards = [
            ("Ziel", "%s:%s" % (cfg["target_host"], cfg["target_port"]), ""),
        ]
        if cfg.get("backup_host"):
            cards.append(("Backup", "%s:%s" % (cfg["backup_host"], cfg["backup_port"]), ""))
        cards += [
            ("Wartend", counts.get("queued", 0) + counts.get("sending", 0) + counts.get("held", 0), ""),
            ("Gedrosselt", counts.get("throttled", 0), ""),
            ("Gescheitert", counts.get("failed", 0), ""),
            ("Zugestellt", counts.get("sent", 0), ""),
            (
                "Letzte Zustellung",
                fmt_time(rt.last_success) if rt.last_success else "noch keine",
                "",
            ),
        ]
        card_html = "".join(
            '<div class="card"><b>%s</b><span>%s</span></div>' % (e(value), e(label))
            for label, value, _ in cards
        )

        tabs = []
        for key, label in TABS:
            amount = (
                sum(view_counts.values())
                if key == "all"
                else sum(view_counts.get(s, 0) for s in self._tab_statuses(key))
            )
            tabs.append(
                '<a href="%s"%s>%s (%d)</a>'
                % (e(self._qs(dict(ctx, f=key))), ' class="on"' if key == flt else "", e(label), amount)
            )

        failed = counts.get("failed", 0)
        resend_all = ""
        if failed:
            resend_all = (
                '<form method="post" action="api/resend-failed">'
                "%s<button>Alle gescheiterten erneut senden (%d)</button></form>"
                % (self._hidden(ctx), failed)
            )

        cams = self.store.camera_map()
        body_rows = "".join(self._row(r, ctx, now, cams) for r in rows)
        if not body_rows:
            body_rows = '<tr><td colspan="7" class="small">Keine Mails in dieser Ansicht.</td></tr>'

        cam_rows = self.store.cameras()
        reviers = sorted({self._revier_of(x) for x in cam_rows}, key=str.casefold)
        if revier and revier not in reviers:
            reviers.append(revier)
        revier_opts = '<option value="">alle Reviere</option>' + "".join(
            '<option value="%s"%s>%s</option>' % (e(x), " selected" if x == revier else "", e(x))
            for x in reviers
        )
        cam_opts = '<option value="">alle Kameras</option>'
        for x in cam_rows:
            if revier and self._revier_of(x) != revier:
                continue
            title = self._cam_title(x["revier"], x["name"]) or x["address"]
            cam_opts += '<option value="%s"%s>%s</option>' % (
                e(x["address"]), " selected" if x["address"] == camera else "", e(title))
        filter_bar = (
            '<form class="filters" method="get" action="mails"><input type="hidden" name="f" value="%s">%s'
            "Revier <select name=\"r\" onchange=\"this.form.c.value='';this.form.submit()\">%s</select>"
            'Kamera <select name="c" onchange="this.form.submit()">%s</select>'
            "<button>Filtern</button>%s</form>"
            % (
                e(flt), self._timebar(preset, start, end), revier_opts, cam_opts,
                '<a href="%s">Filter zur&uuml;cksetzen</a>' % e(self._qs({"f": flt, "r": "", "c": ""}))
                if revier or camera or preset else "",
            )
        )
        deletable = len(self.store.matching_ids(flt, addresses, since=since, until=until))
        total = (
            sum(view_counts.values())
            if flt == "all"
            else sum(view_counts.get(s, 0) for s in self._tab_statuses(flt))
        )
        shown = ""
        if more:
            shown = '<span class="small">Angezeigt werden die neuesten %d von %d Mails.</span> ' % (len(rows), total)
        morebox = ('<p id="more" data-b="%d"><button type="button" id="lbmore">Weitere %d laden</button></p>'
                   % (rows[-1]["id"], self.LIST_PAGE)) if more and rows else ""
        bulk = (
            '<form id="bulk" class="bulk" method="post" action="api/delete-bulk">%s%s'
            '<button type="submit" name="mode" value="selected" class="danger" '
            'onclick="return bulkSelected()">Ausgew&auml;hlte l&ouml;schen</button>'
            '<button type="submit" name="mode" value="all" class="danger"%s '
            "onclick=\"return confirm('Alle %d Mails in dieser Ansicht l&ouml;schen? "
            "Mails, die noch auf die Zustellung warten, bleiben erhalten.')\">"
            "Alle in dieser Ansicht l&ouml;schen (%d)</button></form>"
            % (shown, self._hidden(ctx), "" if deletable else " disabled", deletable, deletable)
        )

        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<base href="%s"><title>AlarmCam</title><style>%s</style></head><body>'
            "<h1>AlarmCam</h1>%s%s"
            '<div class="cards">%s</div>'
            '<div class="small">Aufbewahrung: %s Tage, Speicher: %s von %s</div>'
            '<div class="tabs">%s</div>%s%s%s'
            '<table><thead><tr><th><input type="checkbox" id="selall" title="alle auswählen"></th>'
            "<th>Empfang</th><th>Kamera</th><th>Inhalt</th><th>Status</th>"
            "<th>Details</th><th>Aktionen</th></tr></thead><tbody>%s</tbody></table>%s"
            '<p class="small">Gespeichert wird das Foto mit Status und Empf&auml;ngeradresse, nicht die Mail. '
            "Jedes Foto l&auml;sst sich jederzeit neu senden. Fotos werden nach %s Tagen oder bei vollem "
            "Speicher (&auml;lteste zuerst) gel&ouml;scht, Favoriten bleiben. Hinweis:"
            " RevierSpion zeigt als Zeit den Empfang der Mail, ein "
            "erneutes Senden erscheint dort mit der Uhrzeit des Nachsendens.</p>"
            "%s</body></html>"
            % (
                e(base),
                CSS + PICKER_CSS,
                self._nav("mails", {"r": revier, "c": camera, "z": zeit, "von": von, "bis": bis, "st": flt}),
                "".join(notices),
                card_html,
                e(self.cfg["retention_days"]),
                e(fmt_size(self.store.storage_bytes())),
                e(fmt_size(self.store.cap)),
                "".join(tabs),
                resend_all,
                filter_bar,
                bulk,
                body_rows,
                morebox,
                e(self.cfg["retention_days"]),
                LIGHTBOX,
            )
        )

    @staticmethod
    def _camera_cell(address, cams):
        row = cams.get(address)
        if row is not None and (row["name"] or row["revier"]):
            title = Board._cam_title(row["revier"], row["name"])
            return "<b>%s</b><div class=\"small\">%s</div>" % (e(title), e(address))
        return e(address)

    def _nav(self, active, sel=None):
        new = sum(1 for r in self.store.cameras() if not r["name"] and not r["revier"])
        on = lambda *keys: ' class="on"' if active in keys else ""
        nav = (
            '<div class="tabs"><a href="./"%s>Bilder</a><a href="cameras"%s>Kameras%s</a>'
            '<a href="reviere"%s>Reviere</a><a href="auswertung"%s>Verbindungen</a>'
            '<a href="models"%s>Modelle</a></div>'
            % (on("gallery", "mails"), on("cameras"), " (%d neu)" % new if new else "", on("reviere"),
               on("eval"), on("models"))
        )
        if active in ("gallery", "mails"):
            sel = sel or {}
            common = [(k, sel.get(k, "")) for k in ("r", "c", "z", "von", "bis") if sel.get(k)]
            st = sel.get("st", "")
            gal = common + ([("st", st)] if st and st != "open" else [])
            lst = common + ([("f", st)] if st else [])
            q = lambda items: "?" + "&".join("%s=%s" % (k, quote(str(v), safe="")) for k, v in items) if items else ""
            nav += ('<div class="vsw"><a href="./%s"%s>Galerie</a><a href="mails%s"%s>Liste</a></div>'
                    % (e(q(gal)), on("gallery"), e(q(lst)), on("mails")))
        return nav

    def _filter_form(self, action, ctx, with_status=False, extra="", timebar="", det=None, fav=None, status=None):
        cam_rows = self.store.cameras()
        reviers = sorted({self._revier_of(x) for x in cam_rows}, key=str.casefold)
        if ctx["r"] and ctx["r"] not in reviers:
            reviers.append(ctx["r"])
        revier_opts = '<option value="">alle Reviere</option>' + "".join(
            '<option value="%s"%s>%s</option>' % (e(x), " selected" if x == ctx["r"] else "", e(x))
            for x in reviers
        )
        cam_opts = '<option value="">alle Kameras</option>'
        for x in cam_rows:
            if ctx["r"] and self._revier_of(x) != ctx["r"]:
                continue
            title = self._cam_title(x["revier"], x["name"]) or x["address"]
            cam_opts += '<option value="%s"%s>%s</option>' % (
                e(x["address"]), " selected" if x["address"] == ctx["c"] else "", e(title))
        det_select = ""
        if det is not None:
            det_select = 'Erkennung <select name="e" onchange="this.form.submit()">%s</select>' % "".join(
                '<option value="%s"%s>%s</option>' % (k, " selected" if k == det else "", e(v))
                for k, v in DET_OPTIONS)
        if status is not None:
            det_select = 'Status <select name="st" onchange="this.form.submit()">%s</select> ' % "".join(
                '<option value="%s"%s>%s</option>' % (k, " selected" if k == status else "", e(v))
                for k, v in (("", "Alle"),) + TABS[:1] + TABS[2:]) + det_select
        if fav is not None:
            det_select += (' <label class="favf"><input type="checkbox" name="s" value="1"%s '
                           'onchange="if(this.checked){this.form.z.value=\'\';this.form.von.value=\'\';this.form.bis.value=\'\'}this.form.submit()">'
                           '&#9733; Favoriten</label>' % (" checked" if fav else ""))
        return (
            '<form class="filters" method="get" action="%s">%s%s'
            "Revier <select name=\"r\" onchange=\"this.form.c.value='';this.form.submit()\">%s</select>"
            'Kamera <select name="c" onchange="this.form.submit()">%s</select>%s'
            "<button>Filtern</button>%s</form>"
            % (
                action,
                '<input type="hidden" name="f" value="%s">' % e(ctx["f"]) if with_status else "",
                timebar, revier_opts, cam_opts, det_select, extra,
            )
        )

    def _gallery_page(self, req, query):
        ctx = self._ctx(query)
        base = self._ingress(req) + "/"
        one = lambda k: (query.get(k) or [""])[0][:40]
        zeit = one("z")   # ohne Angabe: alle Bilder
        det = one("e") if one("e") in DET_FILTERS else ""
        fav = one("s") == "1"
        status = one("st") if one("st") in FILTERS and one("st") != "all" else ""
        before = int(one("b")) if one("b").isdigit() else None
        if one("na").isdigit():
            _p, _s, _e, since, until = self._range({"z": zeit, "von": one("von"), "bis": one("bis")})
            rows = self.store.gallery_rows(self._addresses(ctx), since=since, until=until, det=det, fav=fav,
                                           flt=status or "all", limit=100)
            return self._send(req, 200, json.dumps({"n": sum(1 for r in rows if r["id"] > int(one("na")))}),
                              "application/json", {"Cache-Control": "no-store"})
        top = self.store.max_id()
        body = self.render_gallery(base, ctx["r"], ctx["c"], zeit, one("von"), one("bis"), det, fav,
                                   status=status, before=before, frag=one("frag") == "1")
        if one("frag") != "1":
            body = body.replace("</body>", '<i id="topid" data-t="%d" hidden></i></body>' % top, 1)
        self._send(req, 200, body, extra={"Cache-Control": "no-store"})

    @staticmethod
    def _parse_local(text):
        """'2026-11-17T08:30' (datetime-local) -> lokales datetime oder None."""
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def time_range(cls, preset, von="", bis="", now=None):
        """Zeitraum der Bilderseite -> (preset, von, bis) als lokale datetimes; None = offen.
        Voreinstellungen werden bei jedem Aufruf neu berechnet ('heute' ist immer das aktuelle Datum).
        Das Ende ist die letzte Minute des letzten Tages (23:59)."""
        now = now or datetime.now()
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def month_start(d, add=0):
            n = d.year * 12 + d.month - 1 + add
            return datetime(n // 12, n % 12 + 1, 1)

        def last_minute(d):  # letzter Tag vor d, 23:59
            return d - timedelta(minutes=1)

        if preset == "heute":
            return preset, day, last_minute(day + timedelta(days=1))
        if preset == "gestern":
            return preset, day - timedelta(days=1), last_minute(day)
        if preset == "woche":
            mon = day - timedelta(days=day.weekday())
            return preset, mon, last_minute(mon + timedelta(days=7))
        if preset == "monat":
            return preset, month_start(day), last_minute(month_start(day, 1))
        if preset == "quartal":
            first = month_start(day, -((day.month - 1) % 3))
            return preset, first, last_minute(month_start(first, 3))
        if preset == "jahr":
            return preset, datetime(day.year, 1, 1), last_minute(datetime(day.year + 1, 1, 1))
        if preset in ("7t", "30t", "365t"):
            days = {"7t": 7, "30t": 30, "365t": 365}[preset]
            return preset, day - timedelta(days=days - 1), last_minute(day + timedelta(days=1))
        if preset == "12m":
            return preset, month_start(day, -11), last_minute(month_start(day, 1))
        if preset == "24h":
            return preset, (now - timedelta(hours=24)).replace(second=0, microsecond=0), None
        start, end = cls._parse_local(von or ""), cls._parse_local(bis or "")
        if start or end:
            return "x", start, end
        return "", None, None

    def _timebar(self, preset, start, end, all_label="Alle Bilder"):
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M") if d else ""
        presets = "".join(
            '<button type="button" data-z="%s"%s>%s</button>'
            % (v, ' class="on"' if v == preset else "", t)
            for v, t in TIME_CHOICES
        )
        return (
            PICKER_HTML.replace("@PRESETS@", presets.replace(">Alle Bilder<", ">%s<" % e(all_label)))
            .replace("@ALL@", e(all_label))
            .replace("@Z@", e(preset)).replace("@VON@", fmt(start)).replace("@BIS@", fmt(end))
            + "<script>" + PICKER_JS + "</script>"
        )

    EVAL_TABS = (("all", "Alle"), ("problems", "Probleme"), ("tests", "Tests (Mail ohne Anhang)"),
                 ("mails", "Mails mit Foto/Video"))
    EVAL_STATUS = {
        "ok_mail": ("green", "Mail mit Foto/Video ok"),
        "ok_test": ("blue", "Test-Mail ok"),
        "tls_fail": ("red", "SSL/TLS-Handshake nicht ok"),
        "starttls_fail": ("red", "STARTTLS-Handshake nicht ok"),
        "login_fail": ("red", "Login falsch"),
        "locked": ("red", "Adresse gesperrt"),
        "no_login": ("amber", "Abbruch vor dem Login"),
        "no_mail": ("amber", "Abbruch nach Login, Mail nicht angekommen"),
        "rcpt_denied": ("red", "Empf&auml;nger nicht erlaubt"),
    }

    def _eval_page(self, req, query):
        one = lambda k: (query.get(k) or [""])[0][:40]
        ctx = self._ctx(query)
        flt = one("f") if one("f") in dict(self.EVAL_TABS) else "all"
        ctx["f"] = flt
        zeit = one("z") if "z" in query else ("" if one("von") or one("bis") else "7t")
        base = self._ingress(req) + "/"
        self._send(req, 200, self.render_eval(base, ctx, zeit, one("von"), one("bis"), one("fremd") == "1"),
                   extra={"Cache-Control": "no-store"})

    def render_eval(self, base, ctx, zeit="7t", von="", bis="", fremd=False):
        addresses = self._addresses(ctx)
        preset, start, end = self.time_range(zeit, von, bis)
        since = time.mktime(start.timetuple()) if start else None
        until = time.mktime((end + timedelta(minutes=1)).timetuple()) if end else None
        rows = self.store.list_sessions(ctx["f"], addresses, since, until, 500, fremd)
        counts = self.store.session_counts(addresses, since, until, fremd)
        cams = self.store.camera_map()
        ok_mail, ok_test = counts.get("ok_mail", 0), counts.get("ok_test", 0)
        problems = sum(n for k, n in counts.items() if k not in ("ok_mail", "ok_test"))
        cards = (
            '<div class="cards"><div class="card"><b>%d</b><span>Mails mit Foto/Video</span></div>'
            '<div class="card"><b>%d</b><span>Tests (ohne Anhang)</span></div>'
            '<div class="card"><b>%d</b><span>Probleme</span></div></div>' % (ok_mail, ok_test, problems)
        )
        tabs = "".join(
            '<a href="%s"%s>%s</a>' % (
                "auswertung?" + "&".join(
                    "%s=%s" % (k, quote(str(v), safe="")) for k, v in
                    [("f", key), ("r", ctx["r"]), ("c", ctx["c"]), ("z", preset if preset != "x" else "x"),
                     ("von", von if preset == "x" else ""), ("bis", bis if preset == "x" else ""),
                     ("fremd", "1" if fremd else "")] if v != "" or k == "z"),
                ' class="on"' if key == ctx["f"] else "", label)
            for key, label in self.EVAL_TABS
        )
        lines = []
        for r in rows:
            cam = cams.get(r["camera"]) if r["camera"] else None
            if cam:
                who = self._cam_title(cam["revier"], cam["name"]) or r["camera"]
            elif r["camera"]:
                who = r["camera"].split("@")[0] + " (nicht benannt)"
            else:
                guess = [self._cam_title(cams[c]["revier"], cams[c]["name"]) or c.split("@")[0]
                         for c in self.store.cameras_by_ip(r["ip"]) if c in cams]
                who = "unbekannt" + (" - vermutlich %s" % guess[0] if len(guess) == 1 else
                                     (" - eine von: %s" % ", ".join(guess) if guess else ""))
            cls, text = self.EVAL_STATUS.get(r["outcome"], ("grey", e(r["outcome"])))
            parts = [e(r["ip"] or "")]
            if r["enc"]:
                parts.append(e(r["enc"]))
            if r["outcome"] in ("ok_mail", "ok_test"):
                if r["n_images"] or r["n_videos"]:
                    parts.append("%s%s" % ("%d Foto(s)" % r["n_images"] if r["n_images"] else "",
                                           (", " if r["n_images"] and r["n_videos"] else "") +
                                           ("%d Video(s)" % r["n_videos"] if r["n_videos"] else "")))
                parts.append("%.0f kB" % (r["size"] / 1024))
                if r["mail_id"]:
                    parts.append("Mail #%d" % r["mail_id"])
                if r["subject"]:
                    parts.append("Betreff: %s" % e(r["subject"]))
            if r["detail"]:
                parts.append(e(r["detail"]))
            parts.append("%.1f s" % (r["duration"] or 0))
            lines.append(
                "<tr><td style=\"white-space:nowrap\">%s</td><td>%s</td><td><span class=\"badge %s\">%s</span></td><td class=\"small\">%s</td></tr>"
                % (e(time.strftime("%d.%m.%y %H:%M:%S", time.localtime(r["started"]))), e(who), cls, text,
                   " &middot; ".join(parts))
            )
        table = (
            "<table><tr><th>Datum / Uhrzeit</th><th>Kamera</th><th>Status</th><th>Einzelheiten</th></tr>%s</table>"
            % "".join(lines)
        ) if lines else '<p class="small">Keine Eintr&auml;ge in dieser Auswahl.</p>'
        hint = (
            '<p class="small">Jede Verbindung einer Kamera zum Relay wird hier festgehalten: eine Mail mit Foto/Video, '
            "eine Test-Mail (Mail ohne Anhang) oder ein Problem (Handshake, Login, Abbruch). "
            "Verbindungen von Adressen, die keiner Kamera zuzuordnen sind, sind ausgeblendet%s.</p>"
            % (" (aktuell eingeblendet)" if fremd else
               ' - <a href="auswertung?f=%s&amp;z=%s&amp;fremd=1">einblenden</a>' % (quote(ctx["f"]), quote(preset)))
        )
        form = self._filter_form("auswertung", ctx, with_status=True,
                                 extra='<input type="hidden" name="fremd" value="%s">' % ("1" if fremd else ""),
                                 timebar=self._timebar(preset, start, end, "Alle Zeiten"))
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<base href="%s"><title>Auswertung</title><style>%s</style></head><body>'
            "<h1>Auswertung der Verbindungen</h1>%s%s%s<div class=\"tabs\">%s</div>%s%s</body></html>"
            % (e(base), CSS + PICKER_CSS, self._nav("eval"), form, cards, tabs, table, hint)
        )

    GALLERY_PAGE = 500

    def render_gallery(self, base, revier="", camera="", zeit="", von="", bis="", det="", fav=False,
                       status="", before=None, frag=False):
        ctx = {"f": "all", "r": revier, "c": camera}
        addresses = self._addresses(ctx)
        preset, start, end = self.time_range(zeit, von, bis)
        since = time.mktime(start.timetuple()) if start else None
        until = time.mktime((end + timedelta(minutes=1)).timetuple()) if end else None
        cams = self.store.camera_map()
        rows = self.store.gallery_rows(addresses, since=since, until=until, det=det, fav=fav,
                                       flt=status or "all", before_id=before, limit=self.GALLERY_PAGE + 1)
        more = len(rows) > self.GALLERY_PAGE
        rows = rows[:self.GALLERY_PAGE]
        cards = []
        for r in rows:
            mail_id = r["id"]
            if self.store.preview_path(mail_id):
                src = "mail/%d/preview" % mail_id
            elif r["has_file"] and r["preview"] >= 0:
                src = "mail/%d/part/%d" % (mail_id, r["preview"])
            else:
                continue
            cam = cams.get(r["camera"])
            title = self._cam_title(cam["revier"], cam["name"]) if cam else ""
            title = title or (r["camera"] or "").split("@")[0] or "Kamera"
            when = time.strftime("%d.%m.%y %H:%M", time.localtime(r["received"]))
            cards.append(
                '<div class="gc"><a href="%s" class="zoom" %s><img loading="lazy" src="%s" alt="">%s%s</a>'
                '<div class="t">%s%s</div><div class="d">%s</div></div>'
                % (src, zoom_attrs(r, "%s  -  %s" % (title, time.strftime("%d.%m.%y %H:%M:%S", time.localtime(r["received"])))), src,
                   '<span class="dt">%s</span>' % e(det_text(r)) if det_text(r) else "",
                   '<span class="fv">&#9733;</span>' if r["fav"] else "",
                   e(title), det_badge(r) + status_badge(r), e(when))
            )
        last_id = rows[-1]["id"] if rows else 0
        if frag:   # Nachladen: nur die Karten plus Marke, ob es noch mehr gibt
            return "".join(cards) + ('<i id="fragmore" data-b="%d"></i>' % last_id if more else "")
        extra = ""
        if revier or camera or det or fav or status or preset != "":
            extra = '<a href="./">Filter zur&uuml;cksetzen</a>'
        count = "%d%s Foto%s" % (len(cards), "+" if more else "", "" if len(cards) == 1 and not more else "s")
        body = "".join(cards) or (
            '<p class="small">Keine Vorschaubilder in dieser Ansicht. Vorschaubilder entstehen '
            "beim Empfang einer Mail mit Foto.</p>"
        )
        morebox = ('<p id="more" data-b="%d"><button type="button" id="lbmore">Weitere %d laden</button></p>'
                   % (last_id, self.GALLERY_PAGE)) if more else ""
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<base href="%s"><title>Bilder</title><style>%s</style></head><body>'
            "<h1>Kamera-Bilder</h1>%s%s"
            '<div class="sticky"><div class="bar">%s<button type="button" class="cnt" id="lbshow"%s '
            'title="Alle Fotos der Auswahl automatisch durchlaufen lassen">&#9654; %s</button></div></div>'
            '<div class="grid">%s</div>%s%s</body></html>'
            % (e(base), CSS + PICKER_CSS,
               self._nav("gallery", {"r": revier, "c": camera, "z": zeit, "von": von, "bis": bis, "st": status}),
               self._storage_notice(),
               self._filter_form("./", ctx, extra=extra, timebar=self._timebar(preset, start, end), det=det, fav=fav, status=status),
               "" if cards else " disabled", e(count), body, morebox, LIGHTBOX)
        )

    # -- Modelle ----------------------------------------------------------

    def _model_action(self, name, form=None):
        """Knoepfe im Reiter Modelle. Gibt die Meldung fuer die Anzeige zurueck."""
        d = self.detector
        if d is None:
            return "Die Bilderkennung ist aus."
        try:
            if name == "recheck":
                if d.recheck["active"]:
                    return "Die Neu-Pr\u00fcfung l\u00e4uft bereits."
                n = d.recheck_start()
                return "Bestand wird neu gepr\u00fcft (%d Fotos)." % n if n else "Keine Fotos im Bestand."
            if name == "recheck-fav":
                if d.recheck["active"]:
                    return "Die Neu-Pr\u00fcfung l\u00e4uft bereits."
                n = d.recheck_start(favorites_only=True)
                return "Favoriten werden mit dem produktiven Modell gepr\u00fcft (%d Fotos)." % n if n else "Keine Favoriten mit Foto vorhanden."
            if name == "thresholds":
                which = (form or {}).get("which", [""])[0]
                if which not in ("det", "test"):
                    return "Unbekanntes Modell."
                try:
                    th = {k: int((form or {}).get(k, [""])[0]) / 100.0 for k in ("tier", "mensch", "fahrzeug")}
                except ValueError:
                    return "Schwellen: bitte ganze Zahlen von 10 bis 95 eingeben."
                if any(v < 0.10 or v > 0.95 for v in th.values()):
                    return "Schwellen: nur Werte von 10 bis 95 sind m\u00f6glich."
                if not d.set_thresholds(which, th):
                    return "Kein Testmodell vorhanden."
                return "Schwellen gespeichert (%s)." % ("Produktivmodell" if which == "det" else "Testmodell")
            if name == "recheck-cancel":
                d.recheck_cancel()
                return "Neu-Pr\u00fcfung wird abgebrochen."
            if name == "favtest":
                if not _detect.TEST_TAG:
                    return "Kein Testmodell vorhanden."
                if d.favtest["active"]:
                    return "Der Favoriten-Test l\u00e4uft bereits."
                n = d.favtest_start(manual=True)
                return "Favoriten werden gepr\u00fcft (%d Fotos)." % n if n else "Keine Favoriten mit Foto vorhanden."
            if name == "favtest-cancel":
                d.favtest_cancel()
                return "Favoriten-Test abgebrochen."
            os.makedirs(_detect.TEST_DIR, exist_ok=True)
            if name == "delete-test":
                for f in _detect.list_models(_detect.TEST_DIR):
                    os.remove(f)
                d.check()
                return "Testmodell gel\u00f6scht."
            if name == "promote":
                tf = _detect.TEST_PATH
                if not tf or not os.path.isfile(tf):
                    return "Kein Testmodell vorhanden."
                ok, msg, _size = _detect.validate_model(tf)
                if not ok:
                    return "Testmodell nicht nutzbar: " + msg
                test_tag = _detect.TEST_TAG
                n = self.store.promote_test(test_tag, "extern-" + test_tag[5:])
                self._to_previous(_detect.list_models(_detect.MODEL_DIR))
                shutil.move(tf, os.path.join(_detect.MODEL_DIR, os.path.basename(tf)))
                d.check()
                return "Das Testmodell ist jetzt produktiv (%d Favoriten \u00fcbernommen)." % n
            if name == "rollback":
                prev = _detect.list_models(_detect.PREV_DIR)
                if not prev:
                    return "Kein vorheriges Modell vorhanden."
                scratch = os.path.join(_detect.MODEL_DIR, ".tausch")
                os.makedirs(scratch, exist_ok=True)
                for f in _detect.list_models(_detect.MODEL_DIR):
                    shutil.move(f, os.path.join(scratch, os.path.basename(f)))
                shutil.move(prev[0], os.path.join(_detect.MODEL_DIR, os.path.basename(prev[0])))
                self._to_previous([os.path.join(scratch, n) for n in os.listdir(scratch)])
                shutil.rmtree(scratch, ignore_errors=True)
                d.check()
                return "Das vorherige Modell ist wieder produktiv."
            if name == "builtin":
                cur = _detect.list_models(_detect.MODEL_DIR)
                if not cur:
                    return "Es l\u00e4uft bereits das eingebaute Modell."
                self._to_previous(cur)
                d.check()
                return "Das eingebaute Modell ist wieder produktiv."
        except OSError as exc:
            log.error("Modellordner: %r", exc)
            return "Dateivorgang fehlgeschlagen: %s" % exc.strerror
        return ""

    @staticmethod
    def _to_previous(files):
        """Dateien in den Ordner vorher legen (dort bleibt immer nur das zuletzt produktive Modell)."""
        if not files:
            return
        os.makedirs(_detect.PREV_DIR, exist_ok=True)
        for old in _detect.list_models(_detect.PREV_DIR):
            os.remove(old)
        for f in files:
            shutil.move(f, os.path.join(_detect.PREV_DIR, os.path.basename(f)))

    def _model_upload(self, req):
        """Testmodell hochladen: Rohdaten im Body, Dateiname im Kopf X-Filename. Pruefen, dann in den Ordner test legen."""
        def reply(ok, msg, code=200):
            self._send(req, code, json.dumps({"ok": ok, "msg": msg}), "application/json", {"Cache-Control": "no-store"})

        try:
            length = int(req.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", unquote(req.headers.get("X-Filename", ""))).strip("._")
        if length <= 0 or length > _detect.MAX_UPLOAD:
            req.close_connection = True
            return reply(False, "Datei fehlt oder ist gr\u00f6\u00dfer als %d MB." % (_detect.MAX_UPLOAD // 1048576), 413)
        error = ""
        if self.detector is None:
            error = "Die Bilderkennung ist aus."
        elif not name.lower().endswith(".onnx"):
            error = "Nur .onnx-Dateien sind m\u00f6glich."
        tmp = os.path.join(_detect.TEST_DIR, ".upload.tmp")
        sink = None
        try:
            if not error:
                os.makedirs(_detect.TEST_DIR, exist_ok=True)
                sink = open(tmp, "wb")
            left = length
            while left > 0:
                chunk = req.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                left -= len(chunk)
                if sink:
                    sink.write(chunk)
            if sink:
                sink.close()
                sink = None
            if not error and left > 0:
                error = "Upload unvollst\u00e4ndig."
            if not error:
                ok, msg, _size = _detect.validate_model(tmp)
                if not ok:
                    error = "Modell nicht nutzbar: " + msg
            if error:
                if os.path.exists(tmp):
                    os.remove(tmp)
                return reply(False, error)
            for old in _detect.list_models(_detect.TEST_DIR):
                os.remove(old)
            os.replace(tmp, os.path.join(_detect.TEST_DIR, name))
            self.detector.check()
            log.info("Testmodell hochgeladen: %s (%s)", name, fmt_size(length))
            return reply(True, "Testmodell %s ist bereit, die Favoriten werden gepr\u00fcft." % name)
        except OSError as exc:
            log.error("Upload fehlgeschlagen: %r", exc)
            if sink:
                sink.close()
            return reply(False, "Speichern fehlgeschlagen: %s" % exc.strerror, 500)

    UPLOAD_CHUNK_MAX = 32 * 1048576   # so gross darf ein einzelnes Stueck hoechstens sein (der Browser nimmt 10 MB)
    _up_lock = threading.Lock()

    @staticmethod
    def _upload_id(value):
        return value if re.fullmatch(r"[A-Za-z0-9]{6,80}", value or "") else ""

    def _clean_parts(self):
        """Liegengebliebene Teil-Uploads (aelter als ein Tag) entfernen."""
        try:
            for f in os.listdir(_detect.TEST_DIR):
                fp = os.path.join(_detect.TEST_DIR, f)
                if f.startswith(".upload-") and f.endswith(".part") and time.time() - os.path.getmtime(fp) > 86400:
                    os.remove(fp)
        except OSError:
            pass

    def _model_upload_state(self, req, query):
        """Wie viele Bytes eines begonnenen Uploads liegen schon da (zum Fortsetzen nach einem Abbruch)."""
        uid = self._upload_id((query.get("id") or [""])[0])
        got = 0
        if uid:
            try:
                got = os.path.getsize(os.path.join(_detect.TEST_DIR, ".upload-%s.part" % uid))
            except OSError:
                got = 0
        self._send(req, 200, json.dumps({"ok": bool(uid), "received": got}), "application/json", {"Cache-Control": "no-store"})

    def _model_upload_chunk(self, req):
        """Upload in Stuecken: Rohdaten im Body, Kopfzeilen X-Upload-Id, X-Offset, X-Total-Size, X-Filename.
        Das letzte Stueck (Offset + Laenge = Gesamtgroesse) schliesst den Upload ab, prueft das Modell und legt es in test."""
        def reply(ok, msg, code=200, **extra):
            if not ok:
                req.close_connection = True   # ungelesener Body darf die naechste Anfrage nicht stoeren
            body = {"ok": ok, "msg": msg}
            body.update(extra)
            self._send(req, code, json.dumps(body), "application/json", {"Cache-Control": "no-store"})

        uid = self._upload_id(req.headers.get("X-Upload-Id", ""))
        try:
            length = int(req.headers.get("Content-Length") or 0)
            offset = int(req.headers.get("X-Offset", ""))
            total = int(req.headers.get("X-Total-Size", ""))
        except ValueError:
            return reply(False, "Ung\u00fcltige Angaben im Upload.", 400)
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", unquote(req.headers.get("X-Filename", ""))).strip("._")
        if not uid or offset < 0 or total <= 0 or offset + length > total:
            return reply(False, "Ung\u00fcltige Angaben im Upload.", 400)
        if total > _detect.MAX_UPLOAD:
            return reply(False, "Datei ist gr\u00f6\u00dfer als %d MB." % (_detect.MAX_UPLOAD // 1048576), 413)
        if length <= 0 or length > self.UPLOAD_CHUNK_MAX:
            return reply(False, "St\u00fcck fehlt oder ist zu gro\u00df (h\u00f6chstens %d MB)." % (self.UPLOAD_CHUNK_MAX // 1048576), 413)
        if self.detector is None:
            return reply(False, "Die Bilderkennung ist aus.", 400)
        if not name.lower().endswith(".onnx"):
            return reply(False, "Nur .onnx-Dateien sind m\u00f6glich.", 400)
        part = os.path.join(_detect.TEST_DIR, ".upload-%s.part" % uid)
        with self._up_lock:
            try:
                os.makedirs(_detect.TEST_DIR, exist_ok=True)
                if offset == 0:
                    self._clean_parts()
                have = os.path.getsize(part) if os.path.exists(part) else 0
                if offset > have:   # davor fehlt etwas: Client soll ab "received" neu beginnen
                    return reply(False, "St\u00fcck fehlt davor.", 409, received=have)
                with open(part, "r+b" if have else "wb") as sink:
                    sink.seek(offset)
                    sink.truncate(offset)   # Wiederholung desselben Stuecks ueberschreibt
                    left = length
                    while left > 0:
                        chunk = req.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                        sink.write(chunk)
                if left > 0:
                    with open(part, "r+b") as sink:
                        sink.truncate(offset)
                    return reply(False, "St\u00fcck unvollst\u00e4ndig.", 503, received=offset)
                if offset + length < total:
                    return reply(True, "ok", received=offset + length)
                if os.path.getsize(part) != total:
                    os.remove(part)
                    return reply(False, "Upload unvollst\u00e4ndig, bitte noch einmal von vorn.", 400, received=0)
                ok, msg, _size = _detect.validate_model(part)
                if not ok:
                    os.remove(part)
                    return reply(False, "Modell nicht nutzbar: " + msg, 200, received=0)
                for old in _detect.list_models(_detect.TEST_DIR):
                    os.remove(old)
                os.replace(part, os.path.join(_detect.TEST_DIR, name))
                _detect.refresh()
                self.detector.check()
                t_end = time.time() + 3
                while time.time() < t_end and self.detector.favtest["tag"] != _detect.TEST_TAG:
                    time.sleep(0.05)   # damit die Seite nach dem Neuladen schon "prueft Foto ..." zeigt
                log.info("Testmodell hochgeladen: %s (%s, in St\u00fccken)", name, fmt_size(total))
                return reply(True, "Testmodell %s ist bereit, die Favoriten werden gepr\u00fcft." % name, done=True)
            except OSError as exc:
                log.error("Upload fehlgeschlagen: %r", exc)
                return reply(False, "Speichern fehlgeschlagen: %s" % exc.strerror, 500)

    @staticmethod
    def _sec(ms):
        return ("%.1f s" % (ms / 1000.0)).replace(".", ",") if ms else "-"

    def _perf_block(self, st):
        """Leistung der Modelle: Zeit pro Foto (ein Foto nach dem anderen), Threads, Arbeitsspeicher, Rechenzeit pro Tag."""
        per_day = self.store.photos_per_day()
        rows = []
        for label, which, tag in (("Produktiv", "det", st["prod_tag"]), ("Test", "test", st["test_tag"])):
            if which == "test" and not st["test_path"]:
                continue
            n, avg, mx = self.store.perf(which, tag)
            mem = st["mem"].get(tag, {})
            day = ("%d min" % round(per_day * avg / 60000.0)) if n else "-"
            rows.append((label, n, avg, mx, mem, day))
        if not rows:
            return ""
        trs = []
        for label, n, avg, mx, mem, day in rows:
            trs.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                label, e(str(mem.get("threads", st["threads"]))),
                ("%s (%d Fotos)" % (self._sec(avg), n)) if n else "noch keine Messung",
                self._sec(mx),
                ("+%d MB beim Laden, +%d MB Spitze" % (mem.get("load_mb", 0), mem.get("peak_mb", 0))) if mem else "-",
                e(day)))
        note = ""
        if st["test_path"]:
            n, t_ms, d_ms = self.store.fav_perf(st["test_tag"], st["prod_tag"])
            if n and d_ms:
                note = ("<p>Auf denselben %d Favoriten: Test <b>%s</b>, Produktiv <b>%s</b> pro Foto, das Testmodell braucht etwa "
                        "<b>%s</b> so lang.</p>" % (n, self._sec(t_ms), self._sec(d_ms), ("%.1f&times;" % (t_ms / d_ms)).replace(".", ",")))
        return ("<h2>Leistung</h2><table><thead><tr><th>Modell</th><th>Threads</th><th>Zeit pro Foto (&Oslash;)</th>"
                "<th>l&auml;ngstes</th><th>Arbeitsspeicher</th><th>Rechenzeit pro Tag*</th></tr></thead><tbody>%s</tbody></table>%s"
                '<p class="small">Zeit pro Foto = Dekodieren, Auswerten und Rahmen, wall-clock auf %s Thread(s), die letzten Messungen. '
                "Die Thread-Zahl wird beim Start gelesen, eine &Auml;nderung wirkt nach einem Neustart. *Hochrechnung: %.0f Fotos pro Tag "
                "(Schnitt der letzten 7 Tage) mal Zeit pro Foto. Der Arbeitsspeicher ist der des ganzen Add-ons, daher ungef&auml;hr; "
                "Messungen vor 1.1.3 fehlen.</p>" % ("".join(trs), note, e(str(st["threads"])), per_day))

    FLT_MODELS = (("det", "Produktiv"), ("test", "Test"))
    FLT_ARTS = (("fp", "Fehlalarm"), ("miss", "\u00dcbersehen"), ("ok", "Richtig"))
    FLT_CLASSES = (("mensch", "Mensch"), ("tier", "Tier"), ("fahrzeug", "Fahrzeug"))

    @classmethod
    def _flt(cls, query):
        """Filter der Kontrolle aus der Adresse: Modell (fm), Art (fa), Klasse (fk); Unbekanntes wird ignoriert."""
        def pick(key, options):
            v = (query.get(key) or [""])[0]
            return v if v in dict(options) else ""
        return {"m": pick("fm", cls.FLT_MODELS), "a": pick("fa", cls.FLT_ARTS), "k": pick("fk", cls.FLT_CLASSES)}

    @staticmethod
    def _flt_q(flt):
        return "".join("&%s=%s" % (k, flt[v]) for k, v in (("fm", "m"), ("fa", "a"), ("fk", "k")) if flt and flt.get(v))

    @staticmethod
    def _flt_match(flt, truth, v_det, v_test):
        """Passt die Kachel zum Filter? Art und Klasse beurteilen nur Fotos mit eigenem Label."""
        if not (flt["a"] or flt["k"]):
            return True
        if truth is None:
            return False
        for which in ([flt["m"]] if flt["m"] else ["det", "test"]):
            v = v_det if which == "det" else v_test
            if v is None:
                continue
            ok, fp, miss = v
            k, a = flt["k"], flt["a"]
            if a == "fp":
                hit = (k in fp) if k else bool(fp)
            elif a == "miss":
                hit = (k in miss) if k else bool(miss)
            elif a == "ok":
                hit = ok and (not k or k in truth)
            else:
                hit = k in fp or k in miss
            if hit:
                return True
        return False

    def render_models(self, base, message="", only_diff=False, frag="", flt=None):
        flt = flt or {"m": "", "a": "", "k": ""}
        d = self.detector
        st = d.status() if d is not None else None
        if frag:   # Teilstuecke fuer die Live-Aktualisierung im Browser
            if st is None:
                return ""
            favs = self.store.gallery_rows(fav=True, limit=2000)
            if frag == "prog":
                return self._favtest_block(st, favs)
            if frag == "mstat":
                return self._model_line(st, favs, "det") + "<!--sep-->" + (self._model_line(st, favs, "test") if st["test_path"] else "")
            return self._perf_block(st) + self._control_html(st, favs, only_diff, flt)
        try:
            os.makedirs(_detect.TEST_DIR, exist_ok=True)   # Ordner anlegen, damit man ihn per Samba findet
        except OSError:
            pass

        def info(path):
            try:
                return "%s, %s, %s" % (os.path.basename(path), fmt_size(os.path.getsize(path)),
                                       time.strftime("%d.%m.%y %H:%M", time.localtime(os.path.getmtime(path))))
            except OSError:
                return os.path.basename(path)

        refresh = ""
        note = '<div class="info">%s</div>' % e(message) if message else ""
        if st is None:
            return self._models_shell(base, note + '<div class="banner">Die Bilderkennung ist aus (Einstellung &bdquo;Bilderkennung&ldquo;).</div>', "")
        # produktiv
        if st["builtin"]:
            prod = "MegaDetector V6 (eingebaut)"
        else:
            prod = e(os.path.basename(st["prod_path"]))
        warn = "".join('<div class="banner">Nicht ladbar: %s &ndash; %s</div>' % (e(n), e(m)) for n, m in st["bad"])
        buttons = ""
        prev = _detect.list_models(_detect.PREV_DIR)
        if prev:
            buttons += ('<form method="post" action="api/model-rollback" style="display:inline" '
                        'onsubmit="return confirm(\'Vorheriges Modell wiederherstellen?\')">'
                        '<button>Vorheriges Modell wiederherstellen (%s)</button></form> ' % e(os.path.basename(prev[0])))
        if not st["builtin"]:
            buttons += ('<form method="post" action="api/model-builtin" style="display:inline" '
                        'onsubmit="return confirm(\'Wieder das eingebaute Modell verwenden?\')">'
                        '<button>Auf eingebautes Modell zur&uuml;ck</button></form>')
        favs = self.store.gallery_rows(fav=True, limit=2000)
        prod_html = ('<h2>Produktives Modell</h2><p><span class="mname">%s</span><br><span id="sp" class="small">%s</span><br>'
                     '<span class="small">Dieses Modell wertet alle neuen Mails aus. Hauptordner: <code>%s</code></span></p>%s%s%s'
                     % (prod, self._model_line(st, favs, "det"), e(_detect.MODEL_DIR), self._thr_form(st, "det"),
                        '<form method="post" action="api/model-recheck-fav" style="margin:6px 0"><button>Favoriten mit diesem Modell neu pr&uuml;fen</button> '
                        '<span class="small">(speichert auch die Werte unter der Schwelle)</span></form>',
                        '<p>%s</p>' % buttons if buttons else ""))
        # Testmodell
        if st["test_path"]:
            state = '<br><span class="badge red">Fehler: %s</span>' % e(st["test_error"]) if st["test_error"] else ""
            test_head = ('<p><span class="mname">%s</span> <span class="badge green">aktives Testmodell</span>%s<br><span id="stx" class="small">%s</span></p>'
                         % (e(os.path.basename(st["test_path"])), state, self._model_line(st, favs, "test")))
            test_head += self._thr_form(st, "test")
            test_btns = ('<p><form method="post" action="api/model-promote" style="display:inline" '
                         'onsubmit="return confirm(\'Das Testmodell wird produktiv. Das bisherige kommt in den Ordner vorher. Fortfahren?\')">'
                         '<button>Test in Produktion setzen</button></form> '
                         '<form method="post" action="api/model-delete-test" style="display:inline" '
                         'onsubmit="return confirm(\'Testmodell l\\u00f6schen?\')"><button>Testmodell l&ouml;schen</button></form></p>')
        else:
            test_head = '<p class="small">Kein Testmodell vorhanden.</p><span id="stx" hidden></span>'
            test_btns = ""
        upload = ('<p><input type="file" id="mfile" accept=".onnx"> <button type="button" id="mup">Als Testmodell hochladen</button> '
                  '<span id="mstat" class="small"></span><br><span class="small">Die Datei wird in St&uuml;cken zu 10 MB geladen und bei '
                  'einem Abbruch beim n&auml;chsten Versuch fortgesetzt. Oder die Datei direkt in den Ordner <code>%s</code> legen '
                  "(das Add-on schaut alle 30 Sekunden nach). Ein neues Testmodell ersetzt das vorhandene.</span></p>" % e(_detect.TEST_DIR))
        test_block = ("<h2>Testmodell (nur Favoriten)</h2>" + test_head + upload + '<div id="progw">' + self._favtest_block(st, favs) + "</div>" + test_btns)
        # Neu pruefen
        rc = st["recheck"]
        if rc["active"]:
            refresh = '<meta http-equiv="refresh" content="5">'
            recheck = ('<p>L&auml;uft: <b>%d von %d</b> Fotos <progress max="%d" value="%d"></progress></p>'
                       '<form method="post" action="api/model-recheck-cancel"><button>Abbrechen</button></form>'
                       % (rc["done"], rc["total"], max(1, rc["total"]), rc["done"]))
        else:
            fin = (' <span class="small">Zuletzt fertig: %s</span>' % e(fmt_time(rc["finished"]))) if rc["finished"] else ""
            recheck = ('<form method="post" action="api/model-recheck" onsubmit="return confirm(\'Alle Fotos im Bestand neu auswerten?\')">'
                       '<button>Bestand neu pr&uuml;fen</button></form>'
                       '<p class="small">Wertet alle Fotos mit dem produktiven Modell neu aus (etwa eine Sekunde pro Foto, niedrige Priorit&auml;t). '
                       "Das passiert nie von selbst.%s</p>" % fin)
        recheck_block = "<h2>Bestand neu pr&uuml;fen</h2>" + recheck
        live = '<div id="live" data-d="%s">%s%s</div>' % (e(("1" if only_diff else "0") + self._flt_q(flt)), self._perf_block(st),
                                                          self._control_html(st, favs, only_diff, flt))
        script = """<script>(function(){var b=document.getElementById('mup'),f=document.getElementById('mfile'),s=document.getElementById('mstat');
var CH=10*1048576,pw=document.getElementById('progw'),lv=document.getElementById('live');
function get(u,cb){var x=new XMLHttpRequest();x.open('GET',u);x.onload=function(){if(x.status===200)cb(x.responseText);};x.send();}
function line(t,pct,bad){if(!pw)return;pw.innerHTML='<div id="prog" data-sig="up" data-act="1"><p><b'+(bad?' style="color:#c0392b"':'')+'>'+t+'</b>'+(pct>=0?' <progress max="100" value="'+pct+'"></progress> '+pct+' %':'')+'</p></div>';}
if(b){b.onclick=function(){var fl=f.files[0];s.textContent='';if(!fl){s.textContent='Bitte zuerst eine .onnx-Datei w\u00e4hlen.';return;}
b.disabled=true;window.__upl=true;var uid=(fl.name+fl.size+fl.lastModified).replace(/[^A-Za-z0-9]/g,'').slice(-60);if(uid.length<6)uid='upload'+uid;
var tot=fl.size,n=Math.ceil(tot/CH),fails=0;
line('Upload startet \u2026',0);
function fail(t){b.disabled=false;window.__upl=false;line(t,-1,true);}
function send(off){if(off>=tot){return fail('Upload unerwartet beendet.');}
var end=Math.min(off+CH,tot),i=Math.floor(off/CH)+1,x=new XMLHttpRequest();x.open('POST','api/model-upload-chunk');
x.setRequestHeader('X-Upload-Id',uid);x.setRequestHeader('X-Offset',String(off));x.setRequestHeader('X-Total-Size',String(tot));
x.setRequestHeader('X-Filename',encodeURIComponent(fl.name));
x.upload.onprogress=function(ev){if(ev.lengthComputable){var d=off+ev.loaded;line('Upload: Teil '+i+' von '+n,Math.round(d*100/tot));if(d>=tot)line('Upload: Teil '+i+' von '+n+' \u00b7 Modell wird gepr\u00fcft \u2026',100);}};
function retry(msg){fails++;if(fails>3){return fail(msg+' Nach 3 Wiederholungen aufgegeben. Beim n\u00e4chsten Versuch geht es ab hier weiter.');}
line('Teil '+i+' von '+n+' wird wiederholt ('+fails+'/3) \u2026',Math.round(off*100/tot));setTimeout(function(){send(off);},1500);}
x.onload=function(){var r={};try{r=JSON.parse(x.responseText);}catch(e){}
if(x.status===200&&r.ok){fails=0;if(r.done){window.__upl=false;line('Modell ist bereit \u2026',100);location.href='models?m='+encodeURIComponent(r.msg);}else{send(end);}}
else if(x.status===409&&typeof r.received==='number'){send(r.received);}
else if(x.status===200||(x.status>=400&&x.status<500&&x.status!==408&&x.status!==413)){fail(r.msg||('Fehler '+x.status));}
else if(x.status===413&&r.msg&&r.msg.indexOf('Datei')===0){fail(r.msg);}
else{retry(r.msg||('Fehler '+x.status+'.'));}};
x.onerror=function(){retry('Verbindung unterbrochen.');};x.send(fl.slice(off,end));}
get('api/model-upload-state?id='+uid,function(t){var r={};try{r=JSON.parse(t);}catch(e){}var got=(r.received||0);
if(got>=tot)got=0;if(got)line('Setze fort bei '+Math.round(got*100/tot)+' % \u2026',Math.round(got*100/tot));send(got);});};}
if(!pw||!lv)return;
function refreshStat(){get('models?frag=mstat',function(h){var p=h.split('<!--sep-->'),a=document.getElementById('sp'),c=document.getElementById('stx');if(a)a.innerHTML=p[0];if(c&&p[1])c.innerHTML=p[1];});}
function refreshLive(){get('models?frag=live&d='+lv.getAttribute('data-d'),function(l){lv.innerHTML=l;if(window.simAll)window.simAll();});refreshStat();}
window.onLbClose=function(){if(window.lblDirty){window.lblDirty=false;refreshLive();}};
lv.addEventListener('click',function(ev){var bt=ev.target.closest&&ev.target.closest('.lrow button');if(!bt)return;
var row=bt.parentNode,n=bt.getAttribute('data-n'),s=(row.getAttribute('data-lbl')||'').split(',').filter(Boolean),i=s.indexOf(n);
if(n==='leer'){s=i>=0?[]:['leer'];}else{s=s.filter(function(x){return x!=='leer'&&x!==n;});if(i<0)s.push(n);}
row.setAttribute('data-lbl',s.join(','));[].forEach.call(row.querySelectorAll('button'),function(b){b.className=s.indexOf(b.getAttribute('data-n'))>=0?'on':'';});
var x=new XMLHttpRequest();x.open('POST','api/label/'+row.getAttribute('data-id'));x.setRequestHeader('Content-Type','application/x-www-form-urlencoded');
x.onload=function(){refreshLive();};x.send('v='+encodeURIComponent(s.join(',')));});
var sig=(/data-sig="([^"]*)"/.exec(pw.innerHTML)||[])[1]||'';
function tick(){if(document.hidden||window.__upl){setTimeout(tick,3000);return;}
get('models?frag=prog',function(h){var m=/data-sig="([^"]*)"/.exec(h),act=/data-act="1"/.test(h);
var lbo=document.getElementById('lb');if(lbo&&lbo.style.display==='flex'){setTimeout(tick,3000);return;}
if(window.__upl){setTimeout(tick,3000);return;}
pw.innerHTML=h;
if(m&&m[1]!==sig){sig=m[1];refreshLive();}
setTimeout(tick,act?2000:5000);});}
setTimeout(tick,2000);})();</script>"""
        sim_script = """<script>(function(){
var NAMES=['tier','mensch','fahrzeug'],LAB={tier:'Tier',mensch:'Mensch',fahrzeug:'Fahrzeug'};
function data(){var el=document.getElementById('simdata');if(!el)return {};try{return JSON.parse(el.getAttribute('data-j')||'{}');}catch(e){return {};}}
function simAll(){var D=data();[].forEach.call(document.querySelectorAll('.simout'),function(out){var w=out.getAttribute('data-w'),form=out.parentNode,d=D[w];
if(!d||!d.n){out.innerHTML='Noch keine Werte zum Nachrechnen: Favoriten labeln und mit diesem Modell pr\u00fcfen.';return;}
var thr={},i;for(i=0;i<3;i++){var inp=form.querySelector('input[name='+NAMES[i]+']');thr[NAMES[i]]=parseInt(inp.value,10)||0;}
var parts=[],right=0,k;
d.s.forEach(function(r){var ok=true;for(k=0;k<3;k++){if(((r[3+k]>=thr[NAMES[k]])?1:0)!==r[k])ok=false;}if(ok)right++;});
for(k=0;k<3;k++){var fp=0,ms=0;d.s.forEach(function(r){var f=r[3+k]>=thr[NAMES[k]];if(f&&!r[k])fp++;else if(r[k]&&!f)ms++;});
parts.push(LAB[NAMES[k]]+': Fehlalarm <b>'+fp+'</b> \u00b7 \u00fcbersehen <b>'+ms+'</b>');}
var rec=d.r||{};var same=NAMES.every(function(n){return rec[n]===thr[n];});
out.innerHTML=parts.join('<br>')+'<br><b>'+right+' von '+d.n+'</b> Fotos richtig (bei diesen Schwellen, noch nicht gespeichert)<br>Empfehlung: '+NAMES.map(function(n){return LAB[n]+' '+rec[n]+' %';}).join(', ')+(same?'':' <button type="button" class="recbtn">Empfehlung eintragen</button>');
var b=out.querySelector('.recbtn');if(b)b.onclick=function(){NAMES.forEach(function(n){form.querySelector('input[name='+n+']').value=rec[n];});simAll();};});}
window.simAll=simAll;
document.addEventListener('input',function(ev){if(ev.target&&ev.target.closest&&ev.target.closest('.simout')===null&&ev.target.closest('form[action$="model-thresholds"]'))simAll();});
simAll();})();</script>"""
        return self._models_shell(base, note + warn + prod_html + recheck_block + test_block + live + LIGHTBOX + script + sim_script, refresh)

    def _tile_src(self, r):
        if self.store.preview_path(r["id"]):
            return "mail/%d/preview" % r["id"]
        if r["has_file"] and r["preview"] >= 0:
            return "mail/%d/part/%d" % (r["id"], r["preview"])
        return ""

    def _favtest_block(self, st, favs):
        """Stand des Favoriten-Tests mit dem Testmodell: Fortschritt, Zeiten, Button. Wird im Browser alle paar Sekunden ersetzt."""
        if not st["test_path"]:
            return '<div id="prog" data-sig="none" data-act="0"></div>'
        f = st["favtest"]
        now = time.time()
        mmss = lambda sec: "%d:%02d" % (int(max(0, sec)) // 60, int(max(0, sec)) % 60)
        sig = "%d:%d:%d:%s" % (1 if f["active"] else 0, f["done"], int(f["finished"]), f["tag"])
        if f["active"]:
            done, total = f["done"], f["total"]
            avg = (f["ms_sum"] / f["n"]) if f["n"] else 0
            phase = "Modell wird geladen &hellip;" if (f["loading"] and not f["n"]) else "pr&uuml;ft Foto %d von %d" % (min(done + 1, total), total)
            line = "Vergangen %s" % mmss(now - f["started"])
            if avg:
                line += " &middot; &Oslash; %s pro Foto &middot; Rest etwa %s" % (self._sec(avg), mmss(max(0, total - done) * avg / 1000.0))
            cur = ""
            row = next((r for r in favs if r["id"] == f["cur"]), None)
            if row is not None:
                cam = self.store.camera_map().get(row["camera"])
                title = (self._cam_title(cam["revier"], cam["name"]) if cam else "") or (row["camera"] or "").split("@")[0] or "Kamera"
                src = self._tile_src(row)
                cur = ('<div class="small" style="margin-top:6px">%s Gerade: %s, %s</div>'
                       % ('<img src="%s" alt="" style="height:54px;border-radius:6px;vertical-align:middle;margin-right:8px">' % src if src else "",
                          e(title), e(time.strftime("%d.%m.%y %H:%M", time.localtime(row["received"])))))
            body = ('<p><b>%s</b> <progress max="%d" value="%d"></progress></p><p class="small">%s</p>%s'
                    '<form method="post" action="api/model-favtest-cancel"><button>Abbrechen</button></form>'
                    % (phase, max(1, total), done, line, cur))
        else:
            if favs:
                body = ('<form method="post" action="api/model-favtest"><button>Favoriten jetzt pr&uuml;fen</button></form>'
                        '<p class="small">Rechnet alle %d Favoriten mit dem Testmodell neu, auch wenn sie schon ein Ergebnis haben '
                        "(sinnvoll nach einer &Auml;nderung der Threads).</p>" % len(favs))
            else:
                body = '<p class="small">Noch keine Favoriten. Im gro&szlig;en Bild &#9734; antippen.</p>'
            if f["finished"] and f["total"]:
                mem = st["mem"].get(st["test_tag"], {})
                took = f["finished"] - f["started"]
                if f["cancelled"]:
                    last = "abgebrochen nach %d von %d Fotos" % (f["done"], f["total"])
                else:
                    last = "%d Fotos in %s" % (f["total"], mmss(took))
                    if f["n"]:
                        last += " &middot; &Oslash; %s pro Foto" % self._sec(f["ms_sum"] / f["n"])
                    if mem:
                        last += " &middot; Arbeitsspeicher +%d MB Spitze" % mem.get("peak_mb", 0)
                body += '<p class="small">Letzter Lauf (%s): %s</p>' % (e(time.strftime("%d.%m. %H:%M", time.localtime(f["finished"]))), last)
        return '<div id="prog" data-sig="%s" data-act="%d">%s</div>' % (e(sig), 1 if f["active"] else 0, body)

    @staticmethod
    def _truth(row):
        """Eigenes Label eines Favoriten als Menge (leer = "Leer" gelabelt); None, wenn noch nicht gelabelt."""
        lbl = row["lbl"] if "lbl" in row.keys() else ""
        if not lbl:
            return None
        return {n for n in lbl.split(",") if n in DET_BADGES and n not in ("leer", "fehler")}

    @staticmethod
    def _found(row, pre):
        """Was ein Modell gemeldet hat, als Menge; None, wenn es kein (brauchbares) Ergebnis gibt."""
        if pre == "test" and not test_active(row):
            return None
        if sim_ok(row, pre):   # Favorit mit gemerkten Werten: nach den aktuell eingestellten Schwellen
            return {n for n, _c in sim_items(row, pre)}
        label = row[pre + "_label"] if pre + "_label" in row.keys() else ""
        if not label or label == "fehler":
            return None
        return {n for n in label.split(",") if n in DET_BADGES and n not in ("leer", "fehler")}

    @staticmethod
    def _verdict(truth, found):
        """(stimmt, Fehlalarme, uebersehen) oder None, wenn nicht vergleichbar."""
        if truth is None or found is None:
            return None
        fp, miss = found - truth, truth - found
        return (not fp and not miss, fp, miss)

    @staticmethod
    def _verdict_html(v):
        if v is None:
            return ""
        if v[0]:
            return ' <span class="okm">&#10003;</span>'
        names = lambda x: ", ".join(DET_BADGES[n][0] for n in ("mensch", "tier", "fahrzeug") if n in x)
        parts = []
        if v[1]:
            parts.append("Fehlalarm: " + names(v[1]))
        if v[2]:
            parts.append("&uuml;bersehen: " + names(v[2]))
        return ' <b class="diff">&#10007; %s</b>' % "; ".join(parts)

    def _thr_form(self, st, which):
        """Schwellen eines Modells (Tier, Mensch, Fahrzeug in %) einstellen."""
        tag = st["prod_tag"] if which == "det" else st["test_tag"]
        th = _detect.thresholds_for(tag, self.detector.thresholds if self.detector is not None else None)
        own = _detect.has_own_thresholds(tag)
        inputs = "".join('%s <input type="number" name="%s" min="10" max="95" step="1" value="%d" style="width:60px"> %% '
                         % (lab, key, round(th[key] * 100))
                         for key, lab in (("tier", "Tier"), ("mensch", "Mensch"), ("fahrzeug", "Fahrzeug")))
        return ('<form method="post" action="api/model-thresholds" style="margin:6px 0"><input type="hidden" name="which" value="%s">'
                'Schwellen: %s<button>Speichern</button> <span class="small">%s</span><div class="simout small" data-w="%s"></div></form>'
                % (which, inputs, "eigene Einstellung f&uuml;r dieses Modell" if own else "Standard aus der Add-on-Konfiguration", which))

    def _tally(self, favs, pre):
        """Vergleich eines Modells mit den eigenen Labels: (ausgewertet, richtig, mit Fehlalarm, mit Uebersehenem)."""
        n = ok_n = fp_n = miss_n = 0
        for r in favs:
            v = self._verdict(self._truth(r), self._found(r, pre))
            if v is None:
                continue
            n += 1
            ok_n += 1 if v[0] else 0
            fp_n += 1 if v[1] else 0
            miss_n += 1 if v[2] else 0
        return n, ok_n, fp_n, miss_n

    def _model_line(self, st, favs, which):
        """Kennzahlen eines Modells: Dateigroesse, Eingabe, Zeit pro Foto, Trefferquote auf den gelabelten Favoriten."""
        prod = which == "det"
        path = st["prod_path"] if prod else st["test_path"]
        tag = st["prod_tag"] if prod else st["test_tag"]
        parts = []
        try:
            parts.append(fmt_size(os.path.getsize(path)))
        except OSError:
            pass
        px = st["size"] if prod else st["mem"].get(tag, {}).get("size", 0)
        if px:
            parts.append("Eingabe %d px" % px)
        n, avg, _mx = self.store.perf(which, tag)
        parts.append("&Oslash; <b>%s</b> pro Foto (%d Fotos)" % (self._sec(avg), n) if n else "Zeit pro Foto: noch keine Messung")
        labeled = [r for r in favs if self._truth(r) is not None]
        t_n, t_ok, _fp, _miss = self._tally(labeled, "det" if prod else "test")
        if t_n:
            parts.append("Trefferquote <b>%d von %d (%d %%)</b>" % (t_ok, t_n, round(100.0 * t_ok / t_n)))
        else:
            parts.append("Trefferquote: " + ("noch keine Labels" if not labeled else "noch keine Ergebnisse"))
        if not prod or not st["builtin"]:
            try:
                parts.append("Datei vom " + time.strftime("%d.%m.%y %H:%M", time.localtime(os.path.getmtime(path))))
            except OSError:
                pass
        return " &middot; ".join(parts)

    def _label_summary(self, st, favs):
        """Zusammenfassung gegen die eigenen Labels der Favoriten."""
        labeled = [r for r in favs if self._truth(r) is not None]
        if not labeled:
            return ('<p class="small">Noch keine Labels. Im gro&szlig;en Bild oder unter dem Foto <b>Tier, Mensch, Fahrzeug</b> oder <b>Leer</b> antippen '
                    "(im gro&szlig;en Bild auch mit den Tasten 1 bis 4). Danach steht hier, welches Modell wie oft richtig liegt.</p>")

        tally = lambda pre: self._tally(labeled, pre)

        def line(name, t):
            n, ok_n, fp_n, miss_n = t
            if not n:
                return "%s: noch keine Ergebnisse" % name
            return "%s <b>%d von %d richtig</b> (%d mit Fehlalarm, %d mit &uuml;bersehenem)" % (name, ok_n, n, fp_n, miss_n)

        parts = [line("Produktiv", tally("det"))]
        if st["test_path"]:
            parts.append(line("Test", tally("test")))
        n_leer = sum(1 for r in labeled if not self._truth(r))
        tip = ""
        if n_leer < 5:
            tip = (" Tipp: Fehlalarme auf leeren Bildern (z. B. Kameraschrift) siehst du nur, wenn auch einige Fotos ohne Inhalt Favorit mit Label "
                   "&bdquo;Leer&ldquo; sind.")
        return '<p class="small"><b>Gegen deine Labels</b> (%d von %d Favoriten gelabelt): %s.%s</p>' % (len(labeled), len(favs), " &middot; ".join(parts), tip)

    def _control_html(self, st, favs, only_diff, flt=None):
        """Kontrolle Favoriten: Kacheln mit Ergebnis von bisher und Test, eigenes Label und Abweichungen davon."""
        flt = flt or {"m": "", "a": "", "k": ""}
        cams = self.store.camera_map()
        tiles, n_test, n_same, n_shown = [], 0, 0, 0
        for r in favs:
            mail_id = r["id"]
            src = self._tile_src(r)
            if not src:
                continue
            active = test_active(r)
            same = active and ({n for n, _c in det_items(r, "det", True)} == {n for n, _c in det_items(r, "test", True)})
            if active:
                n_test += 1
                n_same += 1 if same else 0
            truth = self._truth(r)
            v_det = self._verdict(truth, self._found(r, "det"))
            v_test = self._verdict(truth, self._found(r, "test"))
            if truth is not None:
                bad = any(v is not None and not v[0] for v in (v_det, v_test))
            else:
                bad = active and not same
            if only_diff and not bad:
                continue
            if not self._flt_match(flt, truth, v_det, v_test):
                continue
            n_shown += 1
            cam = cams.get(r["camera"])
            title = self._cam_title(cam["revier"], cam["name"]) if cam else ""
            title = title or (r["camera"] or "").split("@")[0] or "Kamera"
            cap = "%s  -  %s" % (title, time.strftime("%d.%m.%y %H:%M:%S", time.localtime(r["received"])))
            now_txt = det_text(r, True).replace("\n", ", ") or "-"
            if r["det_ms"]:
                now_txt += " \u00b7 " + self._sec(r["det_ms"])
            lo1 = low_text(r, "det", True)
            lo1 = ' <span class="lo">&middot; unter Schwelle: %s</span>' % e(lo1) if lo1 else ""
            line1 = '<div class="d%s">Bisher: %s%s%s</div>' % (" diff" if v_det is not None and not v_det[0] else "", e(now_txt), lo1, self._verdict_html(v_det))
            if active:
                test_txt = test_text(r, True).replace("\n", ", ")
                if r["test_ms"]:
                    test_txt += " \u00b7 " + self._sec(r["test_ms"])
                if truth is not None:
                    mark = " diff" if (v_test is not None and not v_test[0]) else ""
                else:
                    mark = "" if same else " diff"
                lo2 = low_text(r, "test", True)
                lo2 = ' <span class="lo">&middot; unter Schwelle: %s</span>' % e(lo2) if lo2 else ""
                line2 = '<div class="d%s">Test: %s%s%s</div>' % (mark, e(test_txt), lo2, self._verdict_html(v_test))
            elif st["test_path"]:
                line2 = '<div class="d">Test: wird gepr&uuml;ft &hellip;</div>'
            else:
                line2 = ""
            cur = (r["lbl"] if "lbl" in r.keys() else "") or ""
            have = set(cur.split(","))
            btns = "".join('<button type="button" data-n="%s"%s>%s</button>' % (n, ' class="on"' if n in have else "", lab)
                           for n, lab in (("tier", "Tier"), ("mensch", "Mensch"), ("fahrzeug", "Fahrzeug"), ("leer", "Leer")))
            tiles.append(
                '<div class="gc%s"><a href="%s" class="zoom" %s><img loading="lazy" src="%s" alt=""><span class="fv">&#9733;</span></a>'
                '<div class="t">%s</div>%s%s<div class="lrow" data-id="%d" data-lbl="%s">%s</div></div>'
                % (" diffc" if bad else "", src, zoom_attrs(r, cap, True), src, e(title), line1, line2, mail_id, e(cur), btns))
        if st["test_path"]:
            summary = "%d Favoriten, %d mit Testergebnis, %d gleich, %d abweichend" % (len(favs), n_test, n_same, n_test - n_same)
        else:
            summary = "%d Favoriten (noch kein Testmodell)" % len(favs)
        toggle = '<a href="models?d=%d%s">%s</a>' % (0 if only_diff else 1, self._flt_q(flt), "alle anzeigen" if only_diff else "nur Abweichungen")
        filtered = bool(flt["m"] or flt["a"] or flt["k"])
        shown = (" &middot; %d angezeigt" % n_shown) if (filtered or only_diff) else ""
        tune_html, tune_json = self._tune_html(st, favs)
        return ('<h2>Kontrolle: Favoriten</h2>%s%s<p class="small">%s%s %s</p>%s<div class="grid">%s</div>'
                '<div id="simdata" hidden data-j="%s"></div>'
                % (self._label_summary(st, favs), tune_html, e(summary), shown, toggle, self._filter_html(st, flt, only_diff),
                   "".join(tiles) or ('<p class="small">Keine Fotos f&uuml;r diesen Filter.</p>' if (filtered or only_diff) and favs
                                      else '<p class="small">Noch keine Favoriten. Im gro&szlig;en Bild &#9734; antippen.</p>'),
                   e(tune_json)))

    def _filter_html(self, st, flt, only_diff):
        """Filterzeile der Kontrolle: Modell, Art des Fehlers, Klasse (Antippen schaltet ein/aus)."""
        def chip(key, val, label):
            cur = dict(flt)
            cur[key] = "" if flt[key] == val else val
            href = "models?d=%d%s" % (1 if only_diff else 0, self._flt_q(cur))
            return '<a class="chip%s" href="%s">%s</a>' % (" on" if flt[key] == val else "", e(href), label)
        models = self.FLT_MODELS if st["test_path"] else self.FLT_MODELS[:1]
        groups = [("Modell", "m", models), ("Art", "a", self.FLT_ARTS), ("Klasse", "k", self.FLT_CLASSES)]
        rows = "".join('<span class="small">%s:</span> %s &nbsp; ' % (name, "".join(chip(key, v, lab) for v, lab in opts))
                       for name, key, opts in groups)
        return '<p class="flt">%s<span class="small">(Art und Klasse beurteilen nur Fotos mit deinem Label)</span></p>' % rows

    def _tune_data(self, st, favs):
        """Je Modell die Fotos mit Label und gemerkten Werten: Grundlage fuer Empfehlung und Simulation."""
        out = {}
        for pre in ("det", "test"):
            if pre == "test" and not st["test_path"]:
                continue
            tag = st["prod_tag"] if pre == "det" else st["test_tag"]
            cur = {k: round(v * 100) for k, v in _detect.thresholds_for(tag, self.detector.thresholds if self.detector else None).items()}
            samples, labeled = [], 0
            for r in favs:
                truth = self._truth(r)
                if truth is None or (pre == "test" and not test_active(r)):
                    continue
                labeled += 1
                if sim_ok(r, pre):
                    samples.append(_tune.sample(truth, r[pre + "_low"]))
            rec = dict(cur)
            for k, c in enumerate(_tune.CLASSES):
                b = _tune.best(samples, k)
                if b:
                    rec[c] = b[0]
            out[pre] = {"n": len(samples), "labeled": labeled, "samples": samples, "cur": cur, "rec": rec}
        return out

    def _tune_html(self, st, favs):
        """Schwellen-Hilfe: Vergleich der Modelle bei aktuellen und bei den besten Schwellen (aus den eigenen Labels)."""
        data = self._tune_data(st, favs)
        rows = []
        for pre, name in (("det", "Produktiv"), ("test", "Test")):
            d = data.get(pre)
            if not d:
                continue
            if not d["n"]:
                rows.append('<tr><td>%s</td><td colspan="2" class="small">noch keine Werte (Favoriten neu pr&uuml;fen, Labels setzen)</td></tr>' % name)
                continue
            S = d["samples"]
            cell = lambda thr: "<b>%d von %d</b> richtig &middot; Fehlalarm %d &middot; &uuml;bersehen %d" % (
                (_tune.photos_right(S, thr), d["n"]) + _tune.total_cost(S, thr)[:2])
            fmt = lambda thr: ", ".join("%s %d %%" % (lab, thr[k]) for k, lab in (("tier", "Tier"), ("mensch", "Mensch"), ("fahrzeug", "Fahrzeug")))
            rows.append("<tr><td>%s</td><td>jetzt (%s):<br>%s</td><td>beste (%s):<br>%s</td></tr>"
                        % (name, e(fmt(d["cur"])), cell(d["cur"]), e(fmt(d["rec"])), cell(d["rec"])))
        js = {pre: {"n": d["n"], "s": [list(t) + list(c) for t, c in d["samples"]], "r": d["rec"]} for pre, d in data.items()}
        if not rows:
            return "", json.dumps(js, separators=(",", ":"))
        note = ""
        for pre, name in (("det", "Produktiv"), ("test", "Test")):
            d = data.get(pre)
            if d and d["labeled"] > d["n"]:
                note += " F&uuml;r %d gelabelte Favoriten fehlen beim Modell &bdquo;%s&ldquo; die Werte (Favoriten neu pr&uuml;fen)." % (d["labeled"] - d["n"], name)
        few = " Bei so wenigen Fotos ist die Empfehlung nur eine Orientierung." if any(d["n"] < 20 for d in data.values() if d["n"]) else ""
        html_ = ('<table class="tune"><tr><th></th><th>aktuelle Schwellen</th><th>beste Schwellen nach deinen Labels</th></tr>%s</table>'
                 '<p class="small">Gez&auml;hlt wird pro Foto; &uuml;bersehen z&auml;hlt doppelt so schwer wie ein Fehlalarm. '
                 'Schwellen stellst du oben beim jeweiligen Modell ein; dort siehst du die Wirkung sofort.%s%s</p>' % ("".join(rows), note, few))
        return html_, json.dumps(js, separators=(",", ":"))

    def _models_shell(self, base, body, refresh):
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">%s'
            '<base href="%s"><title>Modelle</title><style>%s'
            ".diff{color:#d9822b;font-weight:700}.diffc{outline:2px solid #f5a524;border-radius:8px}"
            ".chip{display:inline-block;padding:3px 10px;border:1px solid #b9c2cc;border-radius:14px;margin:2px 3px 2px 0;text-decoration:none;color:inherit;font-size:13px}"
            ".chip.on{background:#6e9a35;color:#fff;border-color:#6e9a35}.flt{line-height:2}"
            ".tune{border-collapse:collapse;margin:6px 0}.tune td,.tune th{border:1px solid #d5dbe1;padding:4px 8px;text-align:left;vertical-align:top;font-size:13px}"
            ".simout{margin-top:4px;line-height:1.7}.simout button{margin-left:6px}"
            "h2{margin:22px 0 6px;font-size:17px}</style></head><body><h1>Modelle</h1>%s%s</body></html>"
            % (refresh, e(base), CSS, self._nav("models"), body)
        )

    @staticmethod
    def _note(message):
        if message == "saved":
            return '<div class="info">Gespeichert.</div>'
        if message.startswith("i:"):
            return '<div class="info">%s</div>' % e(message[2:])
        if message.startswith("e:"):
            return '<div class="banner">&#9888; %s</div>' % e(message[2:])
        return ""

    def render_cameras(self, base, message=""):
        rows = self.store.cameras()
        reviere = [r["name"] for r in self.store.reviere()]
        lines = []
        for r in rows:
            fresh = not r["name"] and not r["revier"]
            current = r["revier"] or ""
            opts = '<option value="">(kein Revier)</option>' + "".join(
                '<option value="%s"%s>%s</option>' % (e(x), " selected" if x.casefold() == current.casefold() else "", e(x))
                for x in reviere)
            if current and current.casefold() not in {x.casefold() for x in reviere}:
                opts += '<option value="%s" selected>%s</option>' % (e(current), e(current))
            lines.append(
                "<tr%s><td>%s<input type=\"hidden\" name=\"addr\" value=\"%s\">%s</td>"
                '<td><input name="name" maxlength="80" value="%s" placeholder="%s"></td>'
                '<td><select name="revier">%s</select></td>'
                "<td>%s</td></tr>"
                % (
                    ' class="new"' if fresh else "",
                    e(r["address"]),
                    e(r["address"]),
                    ' <span class="badge amber">neu</span>' if fresh else "",
                    e(r["name"] or ""),
                    e(placeholder_name(r["address"])) if not r["name"] else "Klarname",
                    opts,
                    e(fmt_time(r["last_seen"])) if r["last_seen"] else "noch nie",
                )
            )
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<base href="%s"><title>Kameras</title><style>%s'
            "input,select{width:100%%;box-sizing:border-box;padding:5px 8px;border-radius:6px;"
            "border:1px solid var(--line,#8886);background:var(--card);color:var(--fg)}"
            "tr.new td{background:#f59e0b22}</style></head><body>"
            "<h1>Kameras</h1>%s%s"
            "<p class=\"small\">Jede Empfangsadresse, von der eine Mail ankommt, wird hier "
            "automatisch angelegt. Trage den Klarnamen ein und w&auml;hle das Revier (Reviere pflegst du im Reiter "
            "Reviere). Der Name muss im Revier eindeutig sein. Die Fotos liegen unter "
            "<code>/share/kamera_relay/&lt;Revier&gt;/&lt;Klarname&gt;/</code>, die Ordner werden beim Speichern angepasst.</p>"
            '<form method="post" action="api/cameras">'
            "<table><thead><tr><th>Empfangsadresse</th><th>Klarname</th><th>Revier</th>"
            "<th>Zuletzt</th></tr></thead><tbody>%s</tbody></table>"
            "<p><button>Alles speichern</button></p></form></body></html>"
            % (e(base), CSS, self._nav("cameras"), self._note(message), "".join(lines))
        )

    def render_reviere(self, base, message=""):
        rows = self.store.reviere()
        lines = []
        for r in rows:
            n = r["cameras"]
            lines.append(
                '<tr><td><form method="post" action="api/reviere" class="rv">'
                '<input type="hidden" name="old" value="%s"><input name="name" maxlength="80" value="%s">'
                '<button name="action" value="rename">Umbenennen</button></form></td>'
                "<td>%d</td><td>%s</td></tr>"
                % (e(r["name"]), e(r["name"]), n,
                   '<form method="post" action="api/reviere"><input type="hidden" name="old" value="%s">'
                   '<button name="action" value="delete">L&ouml;schen</button></form>' % e(r["name"]) if not n
                   else '<span class="small">hat Kameras</span>')
            )
        return (
            '<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<base href="%s"><title>Reviere</title><style>%s'
            "input{box-sizing:border-box;padding:5px 8px;border-radius:6px;"
            "border:1px solid var(--line,#8886);background:var(--card);color:var(--fg)}"
            ".rv{display:flex;gap:6px}.rv input{flex:1}</style></head><body>"
            "<h1>Reviere</h1>%s%s"
            "<p class=\"small\">Hier pflegst du die Reviere. Beim Umbenennen wird der Ordner auf dem Share mit umbenannt, "
            "und die Kameras behalten ihre Zuordnung. Ein Revier l&auml;sst sich nur l&ouml;schen, wenn ihm keine Kamera mehr zugeordnet ist. "
            "Welche Kamera zu welchem Revier geh&ouml;rt, stellst du im Reiter Kameras ein.</p>"
            '<form method="post" action="api/reviere" class="rv" style="max-width:420px;margin-bottom:14px">'
            '<input name="name" maxlength="80" placeholder="Neues Revier"><button name="action" value="add">Anlegen</button></form>'
            "<table><thead><tr><th>Revier</th><th>Kameras</th><th></th></tr></thead><tbody>%s</tbody></table></body></html>"
            % (e(base), CSS, self._nav("reviere"), self._note(message), "".join(lines) or
               '<tr><td colspan="3" class="small">Noch keine Reviere angelegt.</td></tr>')
        )

    @staticmethod
    def _tab_statuses(key):
        return {
            "open": ("failed", "discarded", "throttled", "filtered", "stored"),
            "problems": ("failed", "discarded"),
            "throttled": ("throttled",),
            "filtered": ("filtered",),
            "stored": ("stored",),
            "queued": ("queued", "sending", "held"),
            "sent": ("sent",),
        }[key]

    def _row(self, r, ctx, now, cams=None):
        label, color = STATUS.get(r["status"], (r["status"], "grey"))
        mail_id = r["id"]
        date_note = ""
        if r["mail_date"] and abs(r["mail_date"] - r["received"]) > 60:
            date_note = '<div class="small">Mail-Datum: %s</div>' % e(fmt_time(r["mail_date"]))

        content = []
        if r["n_images"]:
            content.append("%d Foto%s" % (r["n_images"], "" if r["n_images"] == 1 else "s"))
        if r["n_videos"]:
            content.append("%d Video%s" % (r["n_videos"], "" if r["n_videos"] == 1 else "s"))
        content_html = e(", ".join(content) or "Mail") + det_badge(r) + '<div class="small">%s</div>' % e(
            fmt_size(r["size"])
        )
        if r["preview_type"] == "image" and self.store.preview_path(mail_id):
            src = "mail/%d/preview" % mail_id
        elif r["has_file"] and r["preview"] >= 0:
            src = "mail/%d/part/%d" % (mail_id, r["preview"])
        else:
            src = ""
        if src and r["preview_type"] == "image":
            cam = cams.get(r["camera"]) if cams else None
            who = self._cam_title(cam["revier"], cam["name"]) if cam else ""
            caption = "%s  -  %s  (#%d)" % (who or r["camera"], fmt_time(r["received"]), mail_id)
            content_html += (
                '<a href="%s" class="zoom" %s><img class="pv" loading="lazy" src="%s"></a>'
                % (src, zoom_attrs(r, caption), src)
            )
        elif src:
            content_html += '<video class="pv" controls preload="metadata" src="%s"></video>' % src

        status = r["status"]
        if status == "failed":
            detail = "%s (%d Versuch%s)" % (
                r["last_error"] or "unbekannter Fehler",
                r["attempts"],
                "" if r["attempts"] == 1 else "e",
            )
        elif status in ("queued", "sending"):
            if r["attempts"]:
                detail = "Versuch %d fehlgeschlagen: %s - naechster Versuch in %s" % (
                    r["attempts"],
                    r["last_error"],
                    fmt_dur(r["next_attempt"] - now),
                )
            else:
                detail = "wartet auf Zustellung"
        elif status == "throttled":
            detail = "Nicht weitergeleitet (Drosselung der Kamera)"
        elif status == "filtered":
            detail = "Nicht weitergeleitet (auf dem Foto wurde nichts erkannt, Filter an)"
        elif status == "held":
            detail = "wartet kurz auf die Bilderkennung"
        elif status == "sent":
            detail = "zugestellt um %s (Versuch %d)%s" % (
                fmt_time(r["finished"]), r["attempts"],
                " ueber den Backup-Server" if r["via"] == "backup" else "",
            )
        else:
            detail = r["last_error"]
        if (status == "sent" and r["det_label"] == "leer"
                and self.cfg.get("detect_filter") == "test"):
            detail += " - Testfilter: wäre nicht weitergeleitet worden"

        actions = []
        if status in ("throttled", "failed", "filtered", "stored", "sent", "discarded") and (
                r["has_file"] or self.store.preview_path(mail_id)):
            actions.append(
                '<form method="post" action="api/resend/%d">%s<button>Neu senden</button></form>'
                % (mail_id, self._hidden(ctx))
            )
        attachments = (r["n_images"] or 0) + (r["n_videos"] or 0)
        if not r["has_file"] and self.store.preview_path(mail_id):
            attachments = 1   # nur noch das Foto ist da
        if (r["has_file"] or self.store.preview_path(mail_id)) and attachments:
            for number in range(1, attachments + 1):
                actions.append(
                    '<a href="mail/%d/download/%d">Download%s</a>'
                    % (mail_id, number, "" if attachments == 1 else " %d" % number)
                )
        actions.append(
            '<form method="post" action="api/delete/%d" onsubmit="return confirm(\'Mail l&ouml;schen?\')">'
            "%s<button>L&ouml;schen</button></form>" % (mail_id, self._hidden(ctx))
        )

        waiting_now = status in ("queued", "sending")
        return (
            '<tr><td><input type="checkbox" class="sel" name="ids" value="%d" form="bulk"%s></td>'
            "<td>%s<div class=\"small\">#%d</div>%s</td><td>%s</td><td>%s</td>"
            '<td><span class="badge %s">%s</span></td><td>%s</td><td>%s</td></tr>'
            % (
                mail_id,
                " disabled" if waiting_now else "",
                e(fmt_time(r["received"])),
                mail_id,
                date_note,
                self._camera_cell(r["camera"], cams or {}),
                content_html,
                color,
                e(label),
                e(detail),
                " ".join(actions),
            )
        )
