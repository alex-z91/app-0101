(()=>{'use strict';
const cfg=window.RIFA_CONFIG, $=id=>document.getElementById(id), money=v=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v), selected=new Set();
let occupied={},current=null,countdownTimer=null;
const notify=(id,message)=>{$(id).textContent=message;};
function paint(){const board=$('board');board.replaceChildren();for(let n=1;n<=100;n++){const btn=document.createElement('button');btn.type='button';btn.className='ticket'+(selected.has(n)?' selected':'');btn.textContent=String(n).padStart(2,'0');btn.setAttribute('aria-label',`Número ${n}`);btn.setAttribute('aria-pressed',String(selected.has(n)));if(occupied[n]){btn.disabled=true;btn.title=occupied[n]==='paid'?'Confirmado':'Reservado'}btn.onclick=()=>{if(selected.has(n))selected.delete(n);else selected.add(n);paint();};board.append(btn);}const nums=[...selected].sort((a,b)=>a-b);$('pickedDisplay').textContent=nums.length?nums.map(n=>String(n).padStart(2,'0')).join(' · '):'Nenhum número selecionado';$('qty').textContent=nums.length;$('total').textContent=money(nums.length*20);$('reserveBtn').disabled=!nums.length||!cfg.enabled;}
async function update(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();const data=await r.json();occupied=data.numbers;for(const n of selected)if(occupied[n])selected.delete(n);$('availabilityText').textContent=`${100-data.occupied} disponíveis · ${data.occupied} reservados/confirmados`;$('amountRaised').textContent=money(data.sold*20);$('paidNumbers').textContent=`${data.sold} de 100 números confirmados`;$('progressText').textContent=data.sold+'%';$('progressFill').style.width=data.sold+'%';paint();}catch(e){$('availabilityText').textContent='Falha ao consultar disponibilidade. Atualize a página.';}}
async function copyPix(){try{await navigator.clipboard.writeText(cfg.pixKey);alert('Chave Pix copiada!');}catch(e){prompt('Copie a chave Pix:',cfg.pixKey);}}
$('copyPix').addEventListener('click',copyPix);$('copyModalPix').addEventListener('click',copyPix);$('refresh').addEventListener('click',update);
$('checkoutForm').addEventListener('submit',async e=>{e.preventDefault();if(!cfg.enabled){notify('formNotice','A campanha ainda não está habilitada para reservas.');return;}const nums=[...selected].sort((a,b)=>a-b);if(!nums.length)return;const button=$('reserveBtn');button.disabled=true;notify('formNotice','Criando sua reserva...');try{const resp=await fetch('/api/orders',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':cfg.csrf},body:JSON.stringify({numbers:nums,name:$('name').value,phone:$('phone').value})});const data=await resp.json();if(!resp.ok)throw Error(data.error||'Não foi possível criar sua reserva.');current=data;sessionStorage.setItem('riandro_order',JSON.stringify({order_id:data.order_id,token:data.token,expires_at:data.expires_at,numbers:data.numbers,amount:data.amount}));selected.clear();showOrder(data);await update();notify('formNotice','Reserva efetuada. Envie seu comprovante dentro do prazo.');}catch(err){notify('formNotice',err.message);}finally{paint();}});
function showOrder(order){current=order;$('orderNumbers').textContent=order.numbers.map(n=>String(n).padStart(2,'0')).join(', ');$('orderAmount').textContent=money(order.amount);$('orderCode').textContent=order.order_id.slice(0,12).toUpperCase();$('receiptNotice').textContent='';$('modal').hidden=false;document.body.style.overflow='hidden';if(countdownTimer)clearInterval(countdownTimer);const tick=()=>{const t=Math.max(0,(order.expires_at||0)-Math.floor(Date.now()/1000));$('countdown').textContent=`${String(Math.floor(t/60)).padStart(2,'0')}:${String(t%60).padStart(2,'0')}`;if(t===0){clearInterval(countdownTimer);$('receiptNotice').textContent='O prazo da reserva terminou. Atualize e escolha novamente, caso ainda esteja disponível.';}};tick();countdownTimer=setInterval(tick,1000);}
$('closeModal').onclick=()=>{$('modal').hidden=true;document.body.style.overflow='';};
$('receiptForm').addEventListener('submit',async e=>{e.preventDefault();if(!current)return;const file=$('receipt').files[0];if(!file)return;if(file.size>5*1024*1024){notify('receiptNotice','O arquivo deve ter no máximo 5 MB.');return;}const form=new FormData();form.append('token',current.token);form.append('receipt',file);$('uploadButton').disabled=true;notify('receiptNotice','Enviando comprovante...');try{const r=await fetch(`/api/orders/${encodeURIComponent(current.order_id)}/receipt`,{method:'POST',headers:{'X-CSRF-Token':cfg.csrf},body:form});const data=await r.json();if(!r.ok)throw Error(data.error||'Falha no envio.');notify('receiptNotice','✓ Comprovante recebido e salvo. A organização fará a conferência.');sessionStorage.removeItem('riandro_order');$('receiptForm').hidden=true;const link=$('waLink');if(cfg.whatsapp){const text=`Olá! Participei da campanha do Riandro. Protocolo: ${current.order_id.slice(0,12).toUpperCase()}. Números: ${current.numbers.join(', ')}. Valor: ${money(current.amount)}. Já enviei meu comprovante para conferência.`;link.href=`https://wa.me/${cfg.whatsapp}?text=${encodeURIComponent(text)}`;link.hidden=false;}await update();}catch(err){notify('receiptNotice',err.message);}finally{$('uploadButton').disabled=false;}});
if(!cfg.enabled){$('formNotice').textContent='Prévia do projeto: escolha os números para testar a interface. Compras e reservas estão desabilitadas até a regularização e a ativação da campanha.';}
try{const stored=JSON.parse(sessionStorage.getItem('riandro_order')||'null');if(stored&&stored.expires_at>Math.floor(Date.now()/1000)){showOrder(stored);$('receiptForm').hidden=false;}}catch(e){}
// Resumo persistente no celular: seleção sempre visível e acesso direto ao checkout.
const mobileBar=document.createElement('div');
mobileBar.className='mobile-checkout-bar';
mobileBar.setAttribute('aria-label','Resumo da sua seleção');
mobileBar.innerHTML='<div class="mobile-summary"><span id="mobileQty">Selecione seus números</span><strong id="mobileTotal">R$ 0,00</strong></div><button id="mobileGo" type="button">Ver seleção ↑</button>';
document.body.append(mobileBar);
$('mobileGo').addEventListener('click',()=>document.querySelector('.checkout').scrollIntoView({behavior:'smooth',block:'start'}));
const refreshMobileSummary=()=>{
 const qty=selected.size;
 $('mobileQty').textContent=qty?qty+' número'+(qty===1?' selecionado':'s selecionados'):'Selecione seus números';
 $('mobileTotal').textContent=money(qty*20);
 $('mobileGo').textContent=qty?'Continuar ↑':'Ver cartela ↑';
 $('mobileGo').onclick=qty?null:()=>document.querySelector('#numeros').scrollIntoView({behavior:'smooth'});
};
const originalPaint=paint;
const originalUpdate=update;
// Atualiza o resumo após cada seleção e atualização remota sem modificar a API.
document.getElementById('board').addEventListener('click',()=>queueMicrotask(refreshMobileSummary));
document.getElementById('refresh').addEventListener('click',()=>queueMicrotask(refreshMobileSummary));
refreshMobileSummary();

update();setInterval(update,20000);
})();