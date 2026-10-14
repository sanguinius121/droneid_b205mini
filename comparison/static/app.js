'use strict';
const names={e200:'AntSDR E200',uart:'Tham chiếu UART',ours:'Bộ thu của chúng ta'};
const colors={e200:'#c15f2c',uart:'#118476',ours:'#366bca'};
const crcLabels={e200:'CRC chưa xác nhận',uart:'CRC do thiết bị báo',ours:'CRC kiểm tra độc lập'};
const $=id=>document.getElementById(id);let state=null,lastSession=null,serial='',autoFit=true;
const map=L.map('map').setView([16.05,108.2],5);
const tiles=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,maxNativeZoom:19,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> Tiles © Esri — Esri, HERE, Garmin, USGS, Intermap, INCREMENT P, NRCan, Esri Japan, METI, Esri China, Esri Korea, Esri Thailand, NGCC, GIS User Community'}).addTo(map);
tiles.on('tileerror',()=>{$('map-error').hidden=false});tiles.on('tileload',()=>{$('map-error').hidden=true});
const groups={},enabled={e200:true,uart:true,ours:true};
for(const source of Object.keys(names)){
 groups[source]=L.layerGroup().addTo(map);
 const card=document.createElement('section');card.className='card';card.style.setProperty('--color',colors[source]);
 card.innerHTML=`<div class="card-head"><label><input type="checkbox" checked data-source="${source}">${names[source]}</label></div><p>${crcLabels[source]}</p><div class="numbers"><div><b id="${source}-messages">0</b><small>MESSAGE</small></div><div><b id="${source}-gps">0</b><small>ĐIỂM GPS</small></div><div><b id="${source}-unique">0</b><small>VỊ TRÍ KHÁC NHAU</small></div></div><div class="card-bottom"><span id="${source}-rate">0,000 msg/s</span><span id="${source}-last">Chưa có bản tin</span></div><p id="${source}-crc" class="hint"></p>`;
 $('cards').appendChild(card);
 card.querySelector('input').onchange=e=>{enabled[source]=e.target.checked;if(enabled[source])groups[source].addTo(map);else map.removeLayer(groups[source])};
}
function currentEvents(){return (state?.events||[]).filter(e=>!serial||e.serial===serial)}
function positionUsable(e){return e.position&&e.crc!=='reported_fail'}
function safeTooltip(source,event){const el=document.createElement('div');el.textContent=`${names[source]} · ${event.serial}\n${event.position?.map(v=>v.toFixed(6)).join(', ')} · ${event.crc}`;el.style.whiteSpace='pre-line';return el}
function redraw(){
 const events=currentEvents();let allPoints=[];
 for(const source of Object.keys(names)){
  const rows=events.filter(e=>e.source===source),gps=rows.filter(positionUsable),unique=new Set(gps.map(e=>e.position.join(',')));
  $(`${source}-messages`).textContent=rows.length;$(`${source}-gps`).textContent=gps.length;$(`${source}-unique`).textContent=unique.size;
  $(`${source}-rate`).textContent=((state.elapsed_s?rows.length/state.elapsed_s:0).toFixed(3))+' msg/s';
  const last=rows.reduce((m,e)=>Math.max(m,e.t),0),end=state.end_monotonic_s||((state.start_monotonic_s||0)+state.elapsed_s);
  $(`${source}-last`).textContent=last?`${Math.max(0,end-last).toFixed(0)} s từ bản tin cuối`:'Chưa có bản tin';
  const good=rows.filter(e=>e.crc==='verified_pass'||e.crc==='reported_pass').length,failed=rows.filter(e=>e.crc==='reported_fail').length;
  $(`${source}-crc`).textContent=source==='e200'?`${rows.length} CRC chưa xác nhận`:`${good} CRC ${source==='ours'?'đã kiểm tra':'flag 1'} · ${failed} flag 0 · ${rows.length-good-failed} chưa rõ`;
  if(source==='ours')$(`${source}-crc`).textContent+=` · ${state.stages?.candidates||0} candidate → ${state.stages?.zc_pass||0} ZC`;
  groups[source].clearLayers();const tracks=new Map();
  for(const e of gps){if(!tracks.has(e.serial))tracks.set(e.serial,[]);tracks.get(e.serial).push(e)}
  for(const points of tracks.values()){
   points.sort((a,b)=>a.t-b.t);let segment=[],previous=null;
   for(const e of points){
    if(previous&&e.t-previous.t>15){if(segment.length>1)L.polyline(segment,{color:colors[source],weight:3,opacity:.7}).addTo(groups[source]);segment=[]}
    segment.push(e.position);previous=e;
    L.circleMarker(e.position,{radius:3,color:colors[source],weight:1,fillOpacity:.65}).bindTooltip(safeTooltip(source,e)).addTo(groups[source]);
   }
   if(segment.length>1)L.polyline(segment,{color:colors[source],weight:3,opacity:.7}).addTo(groups[source]);
   const last=points[points.length-1];L.circleMarker(last.position,{radius:8,color:'#fff',weight:2,fillColor:colors[source],fillOpacity:1}).bindTooltip(safeTooltip(source,last)).addTo(groups[source]);
   if(enabled[source])allPoints.push(...points.map(e=>e.position));
  }
 }
 $('gps-state').textContent=allPoints.length?`${allPoints.length} bản ghi vị trí được hiển thị · ngắt trace khi cách nhau >15 s`:'Chờ tọa độ đầu tiên · không vẽ điểm (0, 0)';
 if(allPoints.length&&autoFit&&$('follow').checked){map.fitBounds(L.latLngBounds(allPoints),{padding:[55,95],maxZoom:18});autoFit=false}
 else if(allPoints.length&&$('follow').checked){map.panInside(allPoints[allPoints.length-1],{padding:[70,100],animate:false})}
}
async function poll(){
 try{
  const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);state=await response.json();
  if(lastSession!==state.session){autoFit=true;lastSession=state.session;serial='';$('serial').value=''}
  const selected=$('serial').value,serials=[...new Set(state.events.map(e=>e.serial))].sort();$('serial').replaceChildren(new Option('Tất cả serial',''),...serials.map(s=>new Option(s,s)));$('serial').value=serials.includes(selected)?selected:'';serial=$('serial').value;
  const labels={idle:'CHƯA THU',preparing:'ĐANG KHỞI TẠO',running:'ĐANG THU',stopping:'ĐANG DỪNG',stopped:'ĐÃ LƯU',error:'LỖI',replay:'REPLAY OFFLINE'};$('status').textContent=labels[state.status]||state.status;
  const active=['preparing','running','stopping'].includes(state.status);$('start').disabled=active||state.replay;$('stop').disabled=!active||state.status==='stopping';$('duration').disabled=active;$('uart-order').disabled=active;
  const seconds=Math.floor(state.elapsed_s);$('clock').textContent=`${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;
  $('session').textContent=state.session||'Chưa bắt đầu phiên thử';$('error').textContent=state.error||'';redraw();
 }catch(e){$('error').textContent='Mất kết nối server: '+e.message}
 setTimeout(poll,1000);
}
async function action(path,body){try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});if(!r.ok){const e=await r.json();throw Error(e.detail||r.status)} }catch(e){$('error').textContent=e.message}}
$('start').onclick=()=>action('/api/start',{seconds:Number($('duration').value),uart_order:$('uart-order').value});$('stop').onclick=()=>action('/api/stop');
$('serial').onchange=()=>{serial=$('serial').value;autoFit=true;redraw()};$('fit').onclick=()=>{autoFit=true;const follow=$('follow').checked;$('follow').checked=true;redraw();$('follow').checked=follow};
function download(data,type,name){const url=URL.createObjectURL(new Blob([data],{type})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
$('json').onclick=()=>{if(state)download(JSON.stringify({...state,events:currentEvents()},null,2),'application/json',(state.session||'droneid')+'.json')};
$('csv').onclick=()=>{if(!state)return;const cols=['source','serial','t','crc','latitude','longitude','frequency','altitude','sequence'];const escape=v=>'"'+String(v??'').replaceAll('"','""')+'"';const data=currentEvents().map(e=>({...e,latitude:e.position?.[0],longitude:e.position?.[1]}));download([cols.join(','),...data.map(e=>cols.map(k=>escape(e[k])).join(','))].join('\r\n'),'text/csv',(state.session||'droneid')+'.csv')};
poll();
