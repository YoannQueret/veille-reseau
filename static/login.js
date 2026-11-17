'use strict';
const form=document.querySelector('#login-form'),button=document.querySelector('#login-submit'),error=document.querySelector('#login-error');
async function checkSession(){
  try{
    const response=await fetch('/api/auth/session');
    if(!response.ok)throw Error('Serveur indisponible.');
    const session=await response.json();
    if(session.authenticated){window.location.replace('/');return;}
    form.hidden=!session.configured;
    document.querySelector('#login-setup').hidden=session.configured;
  }catch(e){error.textContent='Impossible de joindre le serveur. Actualisez la page pour réessayer.';}
}
form.addEventListener('submit',async e=>{
  e.preventDefault();error.textContent='';button.disabled=true;
  try{
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-Veille-Request':'1'},body:JSON.stringify({username:document.querySelector('#username').value,password:document.querySelector('#password').value})});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||'Connexion impossible.');
    document.querySelector('#password').value='';window.location.replace('/');
  }catch(e){error.textContent=e.message;}finally{button.disabled=false;}
});
checkSession();
