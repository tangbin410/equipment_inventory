/* Offline Canvas room renderer. Geometry is normalized from millimetres:
 * width 600, half-depth 300, height 2200. Internal panels illustrate kinds only;
 * their placement, quantity and detailing do not represent surveyed U positions,
 * real ports, capacity or connections.
 */
const HEIGHT = 2200 / 600;
const X_STEP = 1.08;
const Y_STEP = 1.9;
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));

export default class RoomRenderer {
  constructor(canvas, { onSelect = () => {} } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    if (!this.ctx) throw new Error('当前浏览器无法创建机房模型画布');
    this.onSelect = onSelect;
    this.data = { cabinets: [], rows: [], maxPosition: 1 };
    this.selected = null;
    this.row = null;
    this.showLabels = true;
    this.showDevices = true;
    this.yaw = Math.PI + 0.62;
    this.pitch = 0.48;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.view = 'overview';
    this.listeners = [];
    this.hitFaces = [];
    this.frame = 0;
    this.destroyed = false;
    canvas.style.touchAction = 'none';
    canvas.style.cursor = 'grab';
    if (!canvas.hasAttribute('tabindex')) canvas.tabIndex = 0;
    canvas.setAttribute('aria-label', '机房三维柜位模型，拖动旋转，滚轮缩放，点击柜体查看明细；方向键旋转，加减号缩放，Home复位');
    this._listen(canvas, 'pointerdown', e => this._down(e));
    this._listen(canvas, 'pointermove', e => this._move(e));
    this._listen(canvas, 'pointerup', e => this._up(e));
    this._listen(canvas, 'pointercancel', () => this._cancel());
    this._listen(canvas, 'contextmenu', e => e.preventDefault());
    this._listen(canvas, 'wheel', e => {
      e.preventDefault();
      this.zoom = clamp(this.zoom * Math.exp(-e.deltaY * 0.0013), 0.35, 5);
      this.render();
    }, { passive: false });
    this._listen(canvas, 'keydown', e => this._key(e));
    this.resizeObserver = new ResizeObserver(() => this._resize());
    this.resizeObserver.observe(canvas);
    this._resize();
  }

  _listen(target, type, fn, options) {
    target.addEventListener(type, fn, options);
    this.listeners.push(() => target.removeEventListener(type, fn, options));
  }
  _resize() {
    const r = this.canvas.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    this.width = r.width;
    this.height = r.height;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.canvas.width = Math.round(r.width * this.dpr);
    this.canvas.height = Math.round(r.height * this.dpr);
    this.render();
  }
  setData(data) {
    this.data = { cabinets: [], rows: [], maxPosition: 1, ...data };
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.render();
  }
  setView(view) {
    const views = {
      overview: [Math.PI + 0.62, 0.48], top: [0, Math.PI / 2],
      front: [Math.PI, 0.2], back: [0, 0.2]
    };
    if (!views[view]) return;
    [this.yaw, this.pitch] = views[view];
    this.view = view;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.render();
  }
  setRow(row) {
    this.row = row || null;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.render();
  }
  select(id) { this.selected = id || null; this.render(); }
  setShowLabels(show) { this.showLabels = !!show; this.render(); }
  setShowDevices(show) { this.showDevices = !!show; this.render(); }
  reset() { this.setView('overview'); }
  destroy() {
    this.destroyed = true;
    this.listeners.forEach(dispose => dispose());
    this.resizeObserver.disconnect();
    if (this.frame) cancelAnimationFrame(this.frame);
  }
  render() {
    if (this.destroyed || this.frame || !this.width) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      if (!this.destroyed) this._draw();
    });
  }

  _point(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  _down(e) {
    if (e.button !== 0 && e.button !== 2) return;
    this.canvas.focus({ preventScroll: true });
    const p = this._point(e);
    this.drag = { start: p, last: p, moved: false, pan: e.shiftKey || e.button === 2, id: e.pointerId };
    this.canvas.setPointerCapture(e.pointerId);
    this.canvas.style.cursor = 'grabbing';
  }
  _move(e) {
    const p = this._point(e);
    if (!this.drag) {
      this.canvas.style.cursor = this._pick(p) ? 'pointer' : 'grab';
      return;
    }
    const dx = p.x - this.drag.last.x;
    const dy = p.y - this.drag.last.y;
    if (Math.hypot(p.x - this.drag.start.x, p.y - this.drag.start.y) > 5) this.drag.moved = true;
    if (this.drag.pan) {
      this.pan.x += dx;
      this.pan.y += dy;
    } else {
      this.yaw += dx * 0.007;
      this.pitch = clamp(this.pitch + dy * 0.005, 0.13, Math.PI / 2);
      this.view = this.pitch > 1.54 ? 'top' : 'custom';
    }
    this.drag.last = p;
    this.render();
  }
  _up(e) {
    if (!this.drag) return;
    const clicked = !this.drag.moved;
    const p = this._point(e);
    this._cancel();
    if (clicked) {
      const id = this._pick(p);
      if (id) { this.select(id); this.onSelect(id); }
    }
  }
  _cancel() {
    if (this.drag && this.canvas.hasPointerCapture(this.drag.id)) this.canvas.releasePointerCapture(this.drag.id);
    this.drag = null;
    this.canvas.style.cursor = 'grab';
  }
  _key(e) {
    let handled = true;
    switch (e.key) {
      case 'ArrowLeft': this.yaw -= 0.12; break;
      case 'ArrowRight': this.yaw += 0.12; break;
      case 'ArrowUp': this.pitch = clamp(this.pitch - 0.07, 0.13, Math.PI / 2); break;
      case 'ArrowDown': this.pitch = clamp(this.pitch + 0.07, 0.13, Math.PI / 2); break;
      case '+': case '=': this.zoom = clamp(this.zoom * 1.15, 0.35, 5); break;
      case '-': case '_': this.zoom = clamp(this.zoom / 1.15, 0.35, 5); break;
      case 'Home': this.reset(); break;
      default: handled = false;
    }
    if (handled) { e.preventDefault(); this.render(); }
  }

  _local(x, y, z) {
    const dx = x - this.center.x;
    const dy = y - this.center.y;
    const sy = Math.sin(this.yaw), cy = Math.cos(this.yaw);
    const sp = Math.sin(this.pitch), cp = Math.cos(this.pitch);
    const horizontal = cy * dx - sy * dy;
    const away = sy * dx + cy * dy;
    return { x: horizontal, y: sp * away - cp * z, depth: cp * away + sp * z };
  }
  _project(x, y, z) {
    const p = this._local(x, y, z);
    return { x: this.origin.x + p.x * this.scale, y: this.origin.y + p.y * this.scale, depth: p.depth };
  }
  _geometry(cabinet) {
    const y = cabinet.rowIndex * Y_STEP;
    // Every row has A on its outer/upper half and B on its lower half in plan view.
    return { x: (cabinet.index - 1) * X_STEP, y: y + (cabinet.face === 'B' ? 0.5 : 0),
      z: 0, w: 1, d: cabinet.face === 'full' ? 1 : 0.5, h: HEIGHT };
  }
  _fit(cabinets, rows, maxPosition) {
    const minRow = Math.min(...rows);
    const maxRow = Math.max(...rows);
    this.floor = { x: -0.35, y: minRow * Y_STEP - 0.32,
      w: (maxPosition - 1) * X_STEP + 1.7, d: (maxRow - minRow) * Y_STEP + 1.65 };
    const floor = this.floor;
    this.center = { x: floor.x + floor.w / 2, y: floor.y + floor.d / 2 };
    const corners = [];
    for (const x of [floor.x - 0.5, floor.x + floor.w]) {
      for (const y of [floor.y - 0.25, floor.y + floor.d]) {
        for (const z of [0, cabinets.length ? HEIGHT + 0.36 : 0.7]) corners.push(this._local(x, y, z));
      }
    }
    const xs = corners.map(p => p.x), ys = corners.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    this.scale = Math.min((this.width - 72) / Math.max(maxX - minX, 1), (this.height - 76) / Math.max(maxY - minY, 1)) * this.zoom;
    this.scale = Math.max(this.scale, 6);
    this.origin = {
      x: this.width / 2 - (minX + maxX) * this.scale / 2 + this.pan.x,
      y: this.height / 2 - (minY + maxY) * this.scale / 2 + this.pan.y
    };
  }
  _polygon(points, fill, stroke, lineWidth = 1, dashed = false) {
    const ctx = this.ctx;
    ctx.beginPath();
    points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.closePath();
    if (fill) { ctx.fillStyle = fill; ctx.fill(); }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lineWidth;
      ctx.setLineDash(dashed ? [5, 4] : []);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
  _worldPolygon(points, fill, stroke, width = 1, dashed = false) {
    const projected = points.map(p => this._project(...p));
    this._polygon(projected, fill, stroke, width, dashed);
    return projected;
  }
  _face(points, fill, stroke, cabinet, type = 'body', lineWidth = 0.7) {
    const vertices = points.map(p => this._project(...p));
    this.faces.push({ vertices, fill, stroke, cabinet, type, lineWidth,
      depth: vertices.reduce((s, p) => s + p.depth, 0) / vertices.length });
  }
  _box(x, y, z, w, d, h, paint, cabinet, type = 'body') {
    const sx = Math.sin(this.yaw) * Math.cos(this.pitch);
    const sy = Math.cos(this.yaw) * Math.cos(this.pitch);
    const sz = Math.sin(this.pitch);
    if (sz > 0.0001) this._face([[x,y,z+h],[x+w,y,z+h],[x+w,y+d,z+h],[x,y+d,z+h]], paint.top, paint.edge, cabinet, type);
    if (sx > 0.0001) this._face([[x+w,y,z],[x+w,y+d,z],[x+w,y+d,z+h],[x+w,y,z+h]], paint.side, paint.edge, cabinet, type);
    else if (sx < -0.0001) this._face([[x,y+d,z],[x,y,z],[x,y,z+h],[x,y+d,z+h]], paint.side, paint.edge, cabinet, type);
    if (sy > 0.0001) this._face([[x+w,y+d,z],[x,y+d,z],[x,y+d,z+h],[x+w,y+d,z+h]], paint.front, paint.edge, cabinet, type);
    else if (sy < -0.0001) this._face([[x,y,z],[x+w,y,z],[x+w,y,z+h],[x,y,z+h]], paint.back || paint.front, paint.edge, cabinet, type);
  }
  _frontPlane(x, y, z, w, h, fill, stroke, cabinet, type = 'detail') {
    this._face([[x,y,z],[x+w,y,z],[x+w,y,z+h],[x,y,z+h]], fill, stroke, cabinet, type, 0.5);
  }
  _cabinet(cabinet) {
    const g = this._geometry(cabinet);
    const selected = cabinet.id === this.selected;
    const issue = cabinet.issues?.length > 0;
    const kind = cabinet.kind || 'equipment';
    const themes = {
      equipment: { top:'#aec7d8', side:'#7f9cb0', front:'#193b55', back:'#7d99ab', edge:'#347ba6', panel:'#225271', light:'#87aabd' },
      odf: { top:'#b5ded4', side:'#6eada2', front:'#1d5d53', back:'#6d9d96', edge:'#2f9c8d', panel:'#3b8c7c', light:'#b8e3d7' },
      iodf: { top:'#d9c8e8', side:'#a48bb9', front:'#493661', back:'#9880ab', edge:'#9a73b9', panel:'#79608f', light:'#ddcdea' }
    };
    const theme = themes[kind] || themes.equipment;
    const paint = selected
      ? { top:'#8bd5df', side:'#6bb7c8', front:'#17485b', back:'#7395a6', edge:'#168ba7' }
      : issue ? { top:'#ecd5ad', side:'#c4a677', front:theme.front, back:'#a99579', edge:'#e5a336' }
      : { ...theme };
    if (cabinet.face !== 'B') [paint.front, paint.back] = [paint.back, paint.front];
    this._box(g.x,g.y,g.z,g.w,g.d,g.h,paint,cabinet);
    const direction = cabinet.face === 'B' ? 1 : -1;
    if (Math.cos(this.yaw) * Math.cos(this.pitch) * direction <= 0.0001) return;
    const fy = direction > 0 ? g.y + g.d + 0.006 : g.y - 0.006;
    this._frontPlane(g.x+0.045,fy,0.08,0.035,HEIGHT-0.16,theme.light,null,cabinet);
    this._frontPlane(g.x+0.92,fy,0.08,0.035,HEIGHT-0.16,theme.light,null,cabinet);
    this._frontPlane(g.x+0.10,fy,0.12,0.80,0.13,theme.front,theme.edge,cabinet);
    if (!this.showDevices) return;
    if (kind === 'odf' || kind === 'iodf') {
      this._distributionPanels(cabinet,g,fy,direction,theme);
      return;
    }
    const devices = cabinet.devices || [];
    if (!devices.length) {
      // Blank cabinet door detailing does not imply an installed device.
      for (const z of [0.65,1.45,2.25,3.05]) this._frontPlane(g.x+0.11,fy,z,0.78,0.045,'#2f536d',null,cabinet);
      return;
    }
    const spacing = Math.min(0.40, (HEIGHT - 0.5) / devices.length);
    const h = Math.min(0.27, spacing * 0.73);
    devices.forEach((device,i) => {
      const z = HEIGHT - 0.29 - (i+1)*spacing;
      const depth = Math.min(g.d * 0.65, 0.31);
      const y = direction > 0 ? fy - depth + 0.004 : fy - 0.004;
      const panelY = fy + direction*0.009;
      this._box(g.x+0.10,y,z,0.80,depth,h,
        {top:'#a9bcc8',side:'#526e82',front:'#466b85',back:'#466b85',edge:'#304e63'},cabinet,'device');
      this._frontPlane(g.x+0.11,panelY,z+0.025,0.78,h-0.05,theme.panel,theme.light,cabinet,'device');
      if (this.scale > 18) {
        this._frontPlane(g.x+0.16,panelY+direction*0.003,z+h*0.44,0.55,0.022,'#81a6bb',null,cabinet,'device');
        this._frontPlane(g.x+0.77,panelY+direction*0.003,z+h*0.25,0.05,h*0.50,'#5c839a',null,cabinet,'device');
      }
    });
  }
  _distributionPanels(cabinet,g,fy,direction,theme) {
    const y = fy + direction*0.008;
    // Stylized distribution/tray surfaces, never real port counts or capacity.
    if (cabinet.kind === 'odf') {
      this._frontPlane(g.x+0.10,y,0.36,0.10,HEIGHT-0.68,'#2c776b',theme.edge,cabinet,'device');
      for (let z=0.48;z<HEIGHT-0.4;z+=0.48) {
        this._frontPlane(g.x+0.25,y,z,0.61,0.33,theme.panel,theme.light,cabinet,'device');
        this._frontPlane(g.x+0.32,y+direction*0.003,z+0.14,0.46,0.035,theme.light,null,cabinet,'device');
      }
    } else {
      this._frontPlane(g.x+0.11,y,0.36,0.13,HEIGHT-0.68,'#5d4675',theme.edge,cabinet,'device');
      this._frontPlane(g.x+0.77,y,0.36,0.11,HEIGHT-0.68,'#5d4675',theme.edge,cabinet,'device');
      for (let z=0.48;z<HEIGHT-0.4;z+=0.36) {
        this._frontPlane(g.x+0.29,y,z,0.43,0.22,theme.panel,theme.light,cabinet,'device');
        this._frontPlane(g.x+0.34,y+direction*0.003,z+0.095,0.33,0.025,theme.light,null,cabinet,'device');
      }
    }
  }

  _tag(text, x, y, color, selected = false, small = false) {
    const ctx = this.ctx;
    ctx.font = `${selected ? 650 : 500} ${small ? 11 : 12}px "PingFang SC","Microsoft YaHei",sans-serif`;
    const width = ctx.measureText(text).width + 12;
    const height = small ? 18 : 21;
    const r = { x:x-width/2, y:y-height/2, width, height };
    ctx.fillStyle = selected ? '#e6f8fb' : 'rgba(255,255,255,0.92)';
    ctx.strokeStyle = selected ? '#3aa6bd' : '#d6e2e9';
    ctx.lineWidth = selected ? 1.2 : 0.7;
    ctx.beginPath();ctx.roundRect(r.x,r.y,r.width,r.height,5);ctx.fill();ctx.stroke();
    ctx.fillStyle = color;
    ctx.textAlign = 'center';ctx.textBaseline = 'middle';ctx.fillText(text,x,y+0.4);
    return r;
  }
  _overlap(a,b) { return a.x < b.x+b.width+3 && a.x+a.width+3 > b.x && a.y < b.y+b.height+3 && a.y+a.height+3 > b.y; }
  _labels(cabinets) {
    if (!this.showLabels) return;
    const labels = cabinets.filter(c => !c.pendingOverlap).map(c => {
      const g = this._geometry(c);
      const p = this._project(g.x+0.5,g.y+g.d/2,HEIGHT+0.10);
      const text = this.pitch > 1.54 ? c.code.slice(2) : c.code;
      return { c, p, text };
    }).sort((a,b) => Number(b.c.id===this.selected)-Number(a.c.id===this.selected) || b.p.depth-a.p.depth);
    const occupied = [];
    const ctx=this.ctx;
    for (const {c,p,text} of labels) {
      const selected = c.id === this.selected;
      if (!selected && this.scale < 21) continue;
      ctx.font = `${selected?650:500} 12px "PingFang SC","Microsoft YaHei",sans-serif`;
      const width=ctx.measureText(text).width+12;
      const rect={x:p.x-width/2,y:p.y-10.5,width,height:21};
      if (!selected && (rect.x<2||rect.x+width>this.width-2||rect.y<3||rect.y>this.height-20||occupied.some(r=>this._overlap(r,rect)))) continue;
      const color = selected ? '#086680' : c.issues?.length ? '#8d5a20' : c.kind==='odf' ? '#227a6c' : c.kind==='iodf' ? '#79579b' : '#24475b';
      occupied.push(this._tag(text,p.x,p.y,color,selected));
    }
  }
  _inside(point, vertices) {
    let inside = false;
    for(let i=0,j=vertices.length-1;i<vertices.length;j=i++) {
      const a=vertices[i],b=vertices[j];
      if ((a.y>point.y)!==(b.y>point.y) && point.x<(b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x) inside=!inside;
    }
    return inside;
  }
  _pick(p) {
    for(let i=this.hitFaces.length-1;i>=0;i--) if(this._inside(p,this.hitFaces[i].vertices)) return this.hitFaces[i].cabinet.id;
    return null;
  }

  _draw() {
    const ctx=this.ctx;
    ctx.setTransform(this.dpr,0,0,this.dpr,0,0);
    const bg=ctx.createLinearGradient(0,0,0,this.height);bg.addColorStop(0,'#f8fbfd');bg.addColorStop(1,'#edf4f8');
    ctx.fillStyle=bg;ctx.fillRect(0,0,this.width,this.height);
    const cabinets=(this.data.cabinets||[]).filter(c=>!this.row||c.row===this.row);
    let rowNumbers;
    if(this.row) rowNumbers=[this.row.charCodeAt(1)-65];
    else {
      const highest=Math.max(0,...(this.data.cabinets||[]).map(c=>Number(c.rowIndex)||0),...(this.data.rows||[]).map(r=>typeof r==='string'?r.charCodeAt(1)-65:Number(r.rowIndex??r.index??0)));
      rowNumbers=Array.from({length:highest+1},(_,i)=>i);
    }
    const maxPosition=Math.max(1,Number(this.data.maxPosition)||0,...cabinets.map(c=>Number(c.index)||0));
    this._fit(cabinets,rowNumbers,maxPosition);
    const floor=this.floor;
    this._worldPolygon([[floor.x,floor.y,0],[floor.x+floor.w,floor.y,0],[floor.x+floor.w,floor.y+floor.d,0],[floor.x,floor.y+floor.d,0]],'#e5eef3','#c8d9e3',1);
    for(const ri of rowNumbers) for(let n=1;n<=maxPosition;n++) {
      const x=(n-1)*X_STEP,y=ri*Y_STEP;
      this._worldPolygon([[x,y,0.003],[x+1,y,0.003],[x+1,y+1,0.003],[x,y+1,0.003]],'#f0f5f8','#cddde7',0.65);
      const mid=[this._project(x,y+0.5,0.004),this._project(x+1,y+0.5,0.004)];
      ctx.beginPath();ctx.moveTo(mid[0].x,mid[0].y);ctx.lineTo(mid[1].x,mid[1].y);ctx.strokeStyle='#d8e3ea';ctx.lineWidth=.6;ctx.stroke();
    }
    // One unresolved full/half-slot conflict is drawn once as a footprint, not as a real cabinet.
    const blocked=new Map();
    for(const c of cabinets.filter(c=>c.pendingOverlap)) blocked.set(`${c.row}:${c.index}`,c);
    const blockedFaces=[];
    for(const c of blocked.values()) {
      const x=(c.index-1)*X_STEP,y=c.rowIndex*Y_STEP;
      const vertices=this._worldPolygon([[x,y,.009],[x+1,y,.009],[x+1,y+1,.009],[x,y+1,.009]],'rgba(238,185,92,.22)','#c58c32',1.7,true);
      blockedFaces.push({vertices,cabinet:c});
    }
    this.faces=[];
    for(const c of cabinets) if(!c.pendingOverlap) this._cabinet(c);
    this.faces.sort((a,b)=>a.depth-b.depth);
    this.hitFaces=blockedFaces.slice();
    for(const face of this.faces) {
      this._polygon(face.vertices,face.fill,face.stroke,face.lineWidth);
      if(face.type==='body') this.hitFaces.push(face);
    }
    for(const c of blocked.values()) {
      const p=this._project((c.index-1)*X_STEP+.5,c.rowIndex*Y_STEP+.5,.02);
      this._tag(`${c.row}${String(c.index).padStart(2,'0')} 待核实`,p.x,p.y,'#93601c',c.id===this.selected,true);
    }
    for(const ri of rowNumbers) {
      const p=this._project(-.66,ri*Y_STEP+.5,.02);
      if(p.x>12&&p.x<this.width-12&&p.y>12&&p.y<this.height-12) this._tag(`A${String.fromCharCode(65+ri)} 排`,p.x,p.y,'#587082',false,true);
    }
    if(this.pitch>1.54) for(let n=1;n<=maxPosition;n++) {
      const p=this._project((n-1)*X_STEP+.5,rowNumbers[0]*Y_STEP-.53,.02);
      this._tag(String(n).padStart(2,'0'),p.x,p.y,'#687e8e',false,true);
    }
    this._labels(cabinets);
    if(!cabinets.length){ctx.font='16px "PingFang SC","Microsoft YaHei",sans-serif';ctx.textAlign='center';ctx.fillStyle='#668092';ctx.fillText('当前范围没有可定位的机柜或机架记录',this.width/2,this.height/2);}
  }
}
