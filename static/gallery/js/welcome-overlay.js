/* Extracted from: templates/gallery/base.html
   Loaded by a <link>/<script> tag in those template(s); keep the two in sync. */
(function(){var d=document,el=d.getElementById('welcome-overlay');if(!el)return;
function postSeen(){var u=el.getAttribute('data-seen-url');if(!u)return;var nv=new FormData(),c=el.getAttribute('data-csrf');if(c)nv.set('csrfmiddlewaretoken',c);fetch(u,{method:'POST',body:nv,headers:{'X-Requested-With':'XMLHttpRequest'},keepalive:true}).catch(function(){})}
function open(){el.style.display='grid';var bd=d.querySelector('.welcome-backdrop');if(bd)bd.style.display='block';el.setAttribute('aria-hidden','false')}
function seenKey(){return el.getAttribute('data-user-key')||'blaq-welcome-seen'}
function rememberSeen(){try{localStorage.setItem(seenKey(),'1')}catch(e){}}
function close(){el.style.display='none';var bd=d.querySelector('.welcome-backdrop');if(bd)bd.style.display='none';el.setAttribute('aria-hidden','true');postSeen();rememberSeen()}
d.addEventListener('click',function(e){var t=e.target;if(!t)return;if(t.closest&&t.closest('.welcome-skip')){close();return}if(t.closest&&t.closest('.welcome-start')){rememberSeen();return}});
d.addEventListener('keydown',function(e){if(el.getAttribute('aria-hidden')==='true')return;if(e.key==='Escape'){close()}});
var known=null;try{known=localStorage.getItem(seenKey())}catch(e){}
if(el.getAttribute('data-force')==='1'||known!=='1'){open()}})();
