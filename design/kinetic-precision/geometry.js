export const outline = [
  [.94, .95], [-.44, .95], [-.95, .44], [-.95, .1],
  [.44, -.34], [.44, -.47], [-.95, -.47], [-.95, -.95],
  [.44, -.95], [.95, -.44], [.95, -.1], [-.44, .34],
  [-.44, .47], [.94, .47],
];

export function roundedOutline(points = outline, radius = .075, steps = 8) {
  const result = [];
  for (let i = 0; i < points.length; i++) {
    const before = points[(i + points.length - 1) % points.length];
    const point = points[i];
    const after = points[(i + 1) % points.length];
    const incoming = Math.hypot(before[0] - point[0], before[1] - point[1]);
    const outgoing = Math.hypot(after[0] - point[0], after[1] - point[1]);
    const r = Math.min(radius, incoming / 3, outgoing / 3);
    const a = point.map((value, axis) => value + (before[axis] - value) * r / incoming);
    const b = point.map((value, axis) => value + (after[axis] - value) * r / outgoing);
    for (let j = 0; j <= steps; j++) {
      const t = j / steps;
      result.push(a.map((value, axis) => (1-t)**2 * value + 2*(1-t)*t*point[axis] + t*t*b[axis]));
    }
  }
  return result;
}

const cross = (a, b, c) => (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]);

export function triangulate(points) {
  const area = points.reduce((sum, a, i) => { const b = points[(i+1)%points.length]; return sum+a[0]*b[1]-b[0]*a[1]; }, 0);
  const remaining = points.map((_, i) => i);
  if (area < 0) remaining.reverse();
  const triangles = [];
  let guard = points.length ** 2;
  while (remaining.length > 3 && guard-- > 0) {
    let found = false;
    for (let i=0; i<remaining.length; i++) {
      const a = remaining[(i+remaining.length-1)%remaining.length];
      const b = remaining[i];
      const c = remaining[(i+1)%remaining.length];
      if (cross(points[a], points[b], points[c]) <= 1e-10) continue;
      const occupied = remaining.some(j => j!==a && j!==b && j!==c && cross(points[a],points[b],points[j])>=-1e-10 && cross(points[b],points[c],points[j])>=-1e-10 && cross(points[c],points[a],points[j])>=-1e-10);
      if (occupied) continue;
      triangles.push([a,b,c]); remaining.splice(i,1); found = true; break;
    }
    if (!found) throw new Error('Invalid engine outline: triangulation did not converge.');
  }
  if (remaining.length===3) triangles.push([...remaining]);
  return triangles;
}

export function createSlab(height = .22) {
  const points = roundedOutline();
  const triangles = triangulate(points);
  const vertices = [];
  const push = (a,b,c) => {
    const u=b.map((v,i)=>v-a[i]), v=c.map((x,i)=>x-a[i]);
    const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
    const length=Math.hypot(...n);
    if (length < 1e-10) return;
    for(const p of [a,b,c]) vertices.push(...p,...n.map(x=>x/length));
  };
  const rings = [
    { y: -height/2, scale: .965 }, { y: -height/2+.035, scale: 1 },
    { y: height/2-.035, scale: 1 }, { y: height/2, scale: .965 },
  ];
  const vertex = (index, ring) => [points[index][0]*ring.scale,ring.y,points[index][1]*ring.scale];
  for(const [a,b,c] of triangles) {
    push(vertex(a,rings[0]),vertex(b,rings[0]),vertex(c,rings[0]));
    push(vertex(c,rings[3]),vertex(b,rings[3]),vertex(a,rings[3]));
  }
  for(let ring=0;ring<3;ring++) for(let i=0;i<points.length;i++) {
    const next=(i+1)%points.length;
    const a=vertex(i,rings[ring]),b=vertex(next,rings[ring]),c=vertex(next,rings[ring+1]),d=vertex(i,rings[ring+1]);
    push(a,c,b); push(a,d,c);
  }
  return new Float32Array(vertices);
}

export function multiply(a,b) {
  const result=new Float32Array(16);
  for(let column=0;column<4;column++) for(let row=0;row<4;row++) for(let k=0;k<4;k++) result[column*4+row]+=a[k*4+row]*b[column*4+k];
  return result;
}
export function transform(x=0,y=0,z=0,rx=0,ry=0,scale=1) {
  const cx=Math.cos(rx),sx=Math.sin(rx),cy=Math.cos(ry),sy=Math.sin(ry);
  return new Float32Array([cy*scale,0,-sy*scale,0,sy*sx*scale,cx*scale,cy*sx*scale,0,sy*cx*scale,-sx*scale,cy*cx*scale,0,x,y,z,1]);
}
export function perspective(aspect) {
  const f=1/Math.tan(.55/2), near=.1, far=50;
  return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0]);
}
