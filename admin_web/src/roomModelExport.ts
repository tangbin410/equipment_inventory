import rendererSource from './roomModelRenderer.js?raw';
import type { RoomModelData } from './roomModel';

interface ExportMeta { roomName: string; roomCode?: string; completedAt?: string; exportedAt?: string }

function scriptJson(value: unknown): string {
  return JSON.stringify(value).replace(/[<>&\u2028\u2029]/g, character => ({
    '<': '\\u003c', '>': '\\u003e', '&': '\\u0026', '\u2028': '\\u2028', '\u2029': '\\u2029',
  })[character]!);
}

/** Export only the generated model and display metadata; never task records or session state. */
export function buildRoomModelHtml(data: RoomModelData, meta: ExportMeta): string {
  const snapshot = {
    cabinets: data.cabinets.map(object => ({
      id: object.id, code: object.code, kind: object.kind, row: object.row, rowIndex: object.rowIndex,
      index: object.index, face: object.face, barcodes: object.barcodes, issues: object.issues,
      pendingOverlap: object.pendingOverlap,
      devices: object.devices.map(device => ({
        id: device.id, name: device.name, category: device.category, model: device.model,
        barcode: device.barcode, positions: device.positions, cabinetBarcodes: device.cabinetBarcodes || [],
      })),
    })),
    rows: data.rows, maxPosition: data.maxPosition,
    stats: {
      objectCount: data.stats.objectCount, deviceCount: data.stats.deviceCount,
      mappedDeviceCount: data.stats.mappedDeviceCount, unplacedCount: data.stats.unplacedCount,
      issueCount: data.stats.issueCount,
    },
  };
  const payload = scriptJson({ data: snapshot, meta: {
    roomName: meta.roomName, roomCode: meta.roomCode || '', completedAt: meta.completedAt || '',
    exportedAt: meta.exportedAt || new Date().toISOString(),
  } });
  const renderer = rendererSource.replace(/\bexport\s+default\s+class\s+RoomRenderer\b/, 'class RoomRenderer').replace(/<\/script/gi, '<\\/script');
  return `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>机房模型 · 离线查看</title>
<style>${EXPORT_STYLE}</style>
</head>
<body>
<main id="app">
  <header class="page-head"><div><span class="eyebrow">机房模型 · 离线快照</span><h1 id="room-name"></h1><p id="room-meta"></p></div><button id="fullscreen" type="button">全屏查看</button></header>
  <div id="stats" class="stats" aria-label="模型统计"></div>
  <details class="notice"><summary>模型说明 · 47U 与尺寸示意</summary><p>本文件保留导出时已生成的机房模型，离线查看无需登录。位置按现场机架编号排列，不代表机房实测布局。俯视时每排上半格是 A 面，下半格是 B 面，各排顺序相同；正面视角对应 A 面，背面视角对应 B 面。</p><p>尺寸统一使用 MM（宽×深×高）：半深柜 600×300×2200，全深柜 600×600×2200。传输综合柜按 47U 示意，1U = 44.45mm；设备实际 U 位未采集，柜内设备高度与排列顺序仅供示意。ODF / IODF 使用不同外观，不表示真实端口数或容量。多柜设备在各关联柜内均保留，总设备记录数去重。</p></details>
  <section class="workspace" aria-label="机房模型与对象资料">
    <div class="scene-panel">
      <div class="controls"><div id="views" class="view-buttons" aria-label="模型视角"><button type="button" data-view="overview" class="active" aria-pressed="true">总览</button><button type="button" data-view="top" aria-pressed="false">俯视</button><button type="button" data-view="front" aria-pressed="false">正面</button><button type="button" data-view="back" aria-pressed="false">背面</button></div><label>排位 <select id="row-filter"><option value="">全部排</option></select></label><label><input id="labels" type="checkbox" checked>编号</label><label><input id="devices" type="checkbox" checked>设备示意</label><button id="reset" type="button">重置</button></div>
      <div class="canvas-wrap"><canvas id="scene"></canvas><div class="legend"><span class="equipment"></span>传输综合柜 <span class="odf"></span>ODF <span class="iodf"></span>IODF <span class="issue"></span>待核实</div></div>
      <p class="scene-help">拖动旋转 · 滚轮缩放 · Shift / 右键拖动平移 · 点击对象查看资料</p>
    </div>
    <aside class="detail-panel"><div class="panel-heading">对象资料</div><div id="object-detail" class="detail-content"><p class="empty">点击模型对象或搜索结果查看资料。</p></div></aside>
  </section>
  <section class="search-panel"><div class="search-head"><label for="search">查询本机房对象与设备</label><input id="search" type="search" placeholder="输入柜位、设备名称、型号或条码" autocomplete="off"></div><div id="search-results" class="search-results"></div><div class="pagination"><span id="result-count"></span><button id="previous" type="button">上一页</button><span id="page-number"></span><button id="next" type="button">下一页</button></div></section>
  <p id="status" class="status" role="status" aria-live="polite"></p>
</main>
<script id="room-model-data" type="application/json">${payload}</script>
<script>
${renderer}
${EXPORT_CLIENT}
</script>
</body>
</html>`;
}

const EXPORT_STYLE = `
:root{color-scheme:light;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#18344f;background:#f2f7fc;font-size:14px}*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit}button,select,input[type=search]{border:1px solid #cbdceb;border-radius:7px;background:#fff;color:#254d70}button{padding:8px 13px;cursor:pointer}button:hover{border-color:#2a80ca;background:#edf6ff}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #368ad2;outline-offset:2px}button:disabled{opacity:.45;cursor:default}#app{max-width:1680px;margin:0 auto;padding:24px;min-height:100vh;background:#f2f7fc}#app:fullscreen{max-width:none;overflow:auto;padding:22px}.page-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}.eyebrow{font-size:12px;color:#4284b7;letter-spacing:1px}h1{font-size:26px;line-height:1.45;margin:4px 0;overflow-wrap:anywhere}.page-head p{font-size:12px;color:#68829a;margin:5px 0 0;line-height:1.8}#fullscreen{white-space:nowrap;border-color:#2c7cc0;color:#216aa4}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:13px}.stat{background:#fff;border:1px solid #d7e5f0;border-radius:9px;padding:13px 17px;display:flex;align-items:center;justify-content:space-between;gap:12px}.stat span{font-size:13px;color:#607d96}.stat strong{font-size:25px;font-weight:650;color:#175c94}.notice{background:#eaf4fd;border:1px solid #d4e6f6;border-radius:8px;margin-bottom:16px;padding:10px 14px;color:#4c6c86;font-size:12px;line-height:1.8}.notice summary{cursor:pointer;color:#315f84;font-size:13px}.notice p{margin:8px 0 0}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;align-items:stretch}.scene-panel,.detail-panel,.search-panel{border:1px solid #d7e5f0;border-radius:10px;background:#fff;min-width:0;overflow:hidden}.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px;border-bottom:1px solid #e0ebf4}.controls label{display:flex;align-items:center;gap:5px;color:#54718a;font-size:12px;white-space:nowrap}.controls select{padding:7px 9px;max-width:125px}.controls input[type=checkbox]{accent-color:#267cc0}.view-buttons{display:flex;gap:4px}.view-buttons button{padding:7px 11px}.view-buttons .active{color:#fff;background:#267cc0;border-color:#267cc0}.canvas-wrap{position:relative;height:clamp(430px,62vh,760px);min-width:0;background:#f2f7fa}.canvas-wrap canvas{display:block;width:100%;height:100%;touch-action:none}.legend{display:flex;align-items:center;flex-wrap:wrap;gap:6px;position:absolute;left:12px;bottom:10px;padding:7px 9px;border:1px solid #d9e5ec;border-radius:6px;background:rgba(255,255,255,.93);font-size:11px;color:#5e768b;pointer-events:none}.legend span{display:inline-block;width:8px;height:8px;border-radius:2px;margin-left:5px}.legend span:first-child{margin-left:0}.equipment{background:#347ba6}.odf{background:#2f9c8d}.iodf{background:#9a73b9}.issue{background:#e5a336}.scene-help{margin:0;padding:10px 14px;color:#7890a3;font-size:11px;border-top:1px solid #e8eff5;line-height:1.7}.detail-panel{display:flex;flex-direction:column;max-height:calc(clamp(430px,62vh,760px) + 130px)}.panel-heading{padding:14px 16px;border-bottom:1px solid #e0ebf4;color:#244f72;font-weight:650}.detail-content{overflow:auto;padding:15px}.empty{color:#8194a4;font-size:13px;line-height:1.8}.object-title{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px;font-size:17px;font-weight:650;overflow-wrap:anywhere}.kind-tag{font-size:11px;font-weight:400;color:#377cae;background:#edf6fc;border:1px solid #d6e7f3;padding:3px 6px;border-radius:5px}.info-line{display:grid;grid-template-columns:86px minmax(0,1fr);gap:8px;padding:9px 0;border-bottom:1px solid #edf2f6;font-size:12px;line-height:1.7}.info-line span:first-child{color:#7690a5}.info-line span:last-child{color:#35566e;overflow-wrap:anywhere}.object-issue{font-size:12px;line-height:1.8;color:#986321;background:#fff7e9;border:1px solid #f0ddb9;border-radius:6px;padding:8px 10px;margin:10px 0}.device-heading{color:#416985;font-size:13px;margin:17px 0 10px}.device-card{padding:11px;border:1px solid #dfebf3;border-radius:7px;background:#f9fcff;margin:9px 0;line-height:1.8;font-size:12px;overflow-wrap:anywhere}.device-card strong{display:block;color:#254f70;font-size:13px;margin-bottom:3px}.device-card p{margin:2px 0;color:#6b8498}.device-card.focus{border-color:#398fd0;background:#eaf5ff}.search-panel{margin-top:16px;padding:15px}.search-head{display:flex;align-items:center;gap:20px;margin-bottom:12px}.search-head label{font-size:14px;color:#345f80;font-weight:600}.search-head input{padding:10px 12px;flex:1;max-width:590px;min-width:140px}.search-results{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.search-result{display:block;width:100%;text-align:left;padding:11px 12px;min-width:0;background:#fbfdff}.search-result strong{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px;font-weight:600;color:#345c7a}.search-result small{display:block;color:#7a8fa1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:5px;font-size:11px}.pagination{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:13px;color:#7890a2;font-size:12px}.pagination button{padding:6px 10px}#result-count{margin-right:auto}.status{font-size:12px;color:#8c6d3d;margin:10px 0 0;line-height:1.7}.status:empty{display:none}@media(max-width:1060px){.workspace{grid-template-columns:minmax(0,1fr)}.detail-panel{max-height:440px}.canvas-wrap{height:56vh;min-height:390px}.search-results{grid-template-columns:minmax(0,1fr)}}@media(max-width:600px){#app,#app:fullscreen{padding:12px}.page-head{align-items:flex-start;gap:10px}h1{font-size:21px}#fullscreen{font-size:12px;padding:7px 9px}.stats{gap:7px}.stat{padding:10px;display:block}.stat span{display:block;font-size:11px;line-height:1.6}.stat strong{display:block;font-size:22px;margin-top:3px}.controls{gap:9px}.view-buttons{width:100%}.view-buttons button{flex:1}.canvas-wrap{height:50vh;min-height:360px}.search-head{display:block}.search-head label{display:block;margin-bottom:10px}.search-head input{width:100%;max-width:none}.pagination{gap:7px}.legend{left:8px;bottom:8px;font-size:10px}.scene-help{padding:8px 10px}}
#fullscreen{background:#185b91;border-color:#185b91;color:#fff;font-weight:600}#fullscreen:hover{background:#124975;border-color:#124975}
`;

const EXPORT_CLIENT = String.raw`
(() => {
  'use strict';
  const payload = JSON.parse(document.getElementById('room-model-data').textContent);
  const data = payload.data, meta = payload.meta;
  const byId = id => document.getElementById(id);
  const create = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  };
  const kindLabel = kind => ({equipment:'传输综合柜',odf:'ODF',iodf:'IODF'}[kind] || kind);
  const faceLabel = face => face === 'full' ? '全深' : face + ' 面（半深）';
  const formatTime = value => {
    if (!value) return '未记录';
    const timestamp = String(value).trim().replace(/^(\d{4}-\d{2}-\d{2}) /,'$1T');
    const localTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(timestamp);
    const date = new Date(timestamp + (localTime ? '+08:00' : ''));
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {hour12:false,timeZone:'Asia/Shanghai'});
  };
  byId('room-name').textContent = meta.roomName || '未命名机房';
  document.title = (meta.roomName || '机房模型') + ' · 离线快照';
  byId('room-meta').textContent = '地点编码：' + (meta.roomCode || '未填写') + '　｜　盘点完成：' + formatTime(meta.completedAt) + '　｜　导出时间：' + formatTime(meta.exportedAt);
  for (const [label, count] of [['柜位 / 机架编号',data.stats.objectCount],['设备记录（去重）',data.stats.deviceCount],['已关联设备（去重）',data.stats.mappedDeviceCount]]) {
    const card = create('div', null, 'stat');
    card.append(create('span',label),create('strong',count));
    byId('stats').append(card);
  }
  for (const row of data.rows) {
    const option = create('option', row + ' 排');
    option.value = row;
    byId('row-filter').append(option);
  }
  let selectedId = null, selectedDeviceId = null, resultPage = 1;
  const renderer = new RoomRenderer(byId('scene'), {onSelect:id => selectObject(id)});
  renderer.setData(data);
  renderer.setView('overview');
  const views = Array.from(byId('views').querySelectorAll('button'));
  function activeView(view) {
    for (const button of views) {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    }
  }
  for (const button of views) button.addEventListener('click', () => {renderer.setView(button.dataset.view);activeView(button.dataset.view);});
  byId('scene').addEventListener('pointermove', event => {if (event.buttons) activeView('custom');});
  byId('row-filter').addEventListener('change', () => {
    const row = byId('row-filter').value;
    renderer.setRow(row || null);
    const selected = data.cabinets.find(object => object.id === selectedId);
    if (selected && row && selected.row !== row) {
      selectedId = null; selectedDeviceId = null; renderer.select(null); renderDetails();
    }
  });
  byId('labels').addEventListener('change', () => renderer.setShowLabels(byId('labels').checked));
  byId('devices').addEventListener('change', () => renderer.setShowDevices(byId('devices').checked));
  byId('reset').addEventListener('click', () => {
    byId('row-filter').value = '';
    renderer.setRow(null); renderer.select(null); renderer.reset(); activeView('overview');
    selectedId = null; selectedDeviceId = null; renderDetails();
  });
  function selectObject(id, deviceId = null) {
    const object = data.cabinets.find(item => item.id === id);
    if (!object) return;
    const filter = byId('row-filter');
    if (filter.value && filter.value !== object.row) {filter.value = object.row;renderer.setRow(object.row);}
    selectedId = id; selectedDeviceId = deviceId === null ? null : String(deviceId);
    renderer.select(id); renderDetails();
  }
  function infoLine(parent, label, value) {
    const line = create('div',null,'info-line');
    line.append(create('span',label),create('span',value || '未采集'));
    parent.append(line);
  }
  function renderDetails() {
    const panel = byId('object-detail');
    panel.replaceChildren();
    const object = data.cabinets.find(item => item.id === selectedId);
    if (!object) {panel.append(create('p','点击模型对象或搜索结果查看资料。','empty'));return;}
    const heading = create('div',null,'object-title');
    heading.append(create('span',object.code),create('span',kindLabel(object.kind),'kind-tag'));
    panel.append(heading);
    infoLine(panel,'排位',object.row + ' 排 · ' + object.index + ' 位');
    infoLine(panel,'面向',faceLabel(object.face));
    infoLine(panel,'尺寸（MM）',object.face === 'full' ? '600×600×2200' : '600×300×2200');
    const hasDeviceBarcodes = object.devices.some(device => device.cabinetBarcodes.length);
    infoLine(panel,'柜 / 架条码',object.barcodes.join('、') || (hasDeviceBarcodes ? '见下方设备关联条码' : ''));
    if (object.kind === 'equipment') infoLine(panel,'关联设备',String(object.devices.length));
    else {
      infoLine(panel,'用途',object.kind === 'odf' ? '光缆配线' : '光纤成端');
      infoLine(panel,'面板示意','独立机架面板示意，不代表真实端口数或容量');
    }
    if (object.pendingOverlap) panel.append(create('p','该位置的身份或占位待核实，场景仅显示待核实底框。','object-issue'));
    for (const issue of object.issues) panel.append(create('p',issue,'object-issue'));
    if (object.kind !== 'equipment') return;
    panel.append(create('h2','关联设备资料','device-heading'));
    if (!object.devices.length) panel.append(create('p','此对象没有关联的设备记录。','empty'));
    const devices = [...object.devices];
    if (selectedDeviceId !== null) devices.sort((a,b) => Number(String(b.id) === selectedDeviceId) - Number(String(a.id) === selectedDeviceId));
    for (const device of devices) {
      const card = create('article',null,'device-card');
      if (String(device.id) === selectedDeviceId) card.classList.add('focus');
      card.append(create('strong',device.name || '未命名设备'));
      card.append(create('p','类别：' + (device.category || '未填写') + ' · 型号：' + (device.model || '未填写')));
      card.append(create('p','资产条码：' + (device.barcode || '未采集')));
      card.append(create('p','所属机柜位：' + (device.positions.join('、') || '未采集')));
      if (device.cabinetBarcodes.length) card.append(create('p','设备反馈柜条码：' + device.cabinetBarcodes.join('、')));
      panel.append(card);
    }
  }
  function searchResults() {
    const query = byId('search').value.trim().toLowerCase();
    const matches = [];
    for (const object of data.cabinets) {
      const position = object.code + ' · ' + kindLabel(object.kind);
      const objectText = [position,object.row,faceLabel(object.face),...object.barcodes].join(' ').toLowerCase();
      if (!query || objectText.includes(query)) matches.push({objectId:object.id,deviceId:null,title:position,detail:'柜 / 架条码：' + (object.barcodes.join('、') || '未采集')});
      if (!query) continue;
      for (const device of object.devices) {
        const text = [position,device.name,device.category,device.model,device.barcode,...device.positions,...device.cabinetBarcodes].join(' ').toLowerCase();
        if (text.includes(query)) matches.push({objectId:object.id,deviceId:device.id,title:device.name || '未命名设备',detail:position + ' · ' + (device.model || '未填写型号') + ' · ' + (device.barcode || '未采集条码')});
      }
    }
    return matches;
  }
  function renderResults() {
    const results = searchResults(), totalPages = Math.max(1,Math.ceil(results.length / 20));
    resultPage = Math.min(resultPage,totalPages);
    const list = byId('search-results'); list.replaceChildren();
    for (const result of results.slice((resultPage-1)*20,resultPage*20)) {
      const button = create('button',null,'search-result'); button.type = 'button';
      button.append(create('strong',result.title),create('small',result.detail));
      button.addEventListener('click', () => selectObject(result.objectId,result.deviceId));
      list.append(button);
    }
    if (!results.length) list.append(create('p','没有符合条件的对象或设备。','empty'));
    byId('result-count').textContent = '共 ' + results.length + ' 项结果';
    byId('page-number').textContent = resultPage + ' / ' + totalPages;
    byId('previous').disabled = resultPage <= 1;
    byId('next').disabled = resultPage >= totalPages;
  }
  byId('search').addEventListener('input', () => {resultPage = 1;renderResults();});
  byId('previous').addEventListener('click', () => {if (resultPage > 1) {resultPage -= 1;renderResults();}});
  byId('next').addEventListener('click', () => {resultPage += 1;renderResults();});
  renderResults();
  const fullscreen = byId('fullscreen'), app = byId('app');
  if (!app.requestFullscreen) {fullscreen.disabled = true;fullscreen.title = '当前浏览器不支持全屏';}
  fullscreen.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await app.requestFullscreen();
      byId('status').textContent = '';
    } catch {byId('status').textContent = '当前浏览器未能切换全屏，可使用浏览器全屏功能。';}
  });
  document.addEventListener('fullscreenchange', () => {fullscreen.textContent = document.fullscreenElement ? '退出全屏' : '全屏查看';});
  window.addEventListener('pagehide', () => renderer.destroy(), {once:true});
})();
`;
