import test from 'node:test';
import assert from 'node:assert/strict';
import { outline, roundedOutline, triangulate, createSlab, multiply, transform, perspective } from './geometry.js';
import { samples } from './samples.js';

test('rounded monogram retains its bounds and a valid concave outline',()=>{
  const points=roundedOutline();
  assert.equal(points.length,outline.length*9);
  assert(points.every(p=>p.every(Number.isFinite)));
  assert(points.every(p=>p.every(v=>Math.abs(v)<=.951)));
});
test('ear clipping covers the rounded concave shape without filling its cutouts',()=>{
  const points=roundedOutline(), triangles=triangulate(points);
  assert.equal(triangles.length,points.length-2);
  const polygonArea=Math.abs(points.reduce((s,a,i)=>{const b=points[(i+1)%points.length];return s+a[0]*b[1]-b[0]*a[1];},0))/2;
  const area=triangles.reduce((s,[a,b,c])=>{const p=points[a],q=points[b],r=points[c];return s+Math.abs((q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0]))/2;},0);
  assert(Math.abs(polygonArea-area)<1e-7);
});
test('each GPU vertex has finite coordinates and a unit normal',()=>{
  const mesh=createSlab();assert.equal(mesh.length%18,0);assert(mesh.length>1000);assert(mesh.length<50000);
  for(let i=0;i<mesh.length;i+=6){assert([...mesh.slice(i,i+6)].every(Number.isFinite));assert(Math.abs(Math.hypot(...mesh.slice(i+3,i+6))-1)<1e-6);assert(Math.abs(mesh[i+1])<=.111);}
});
test('transform composition retains translation and identity',()=>{
  const identity=transform(), t=transform(1,2,3);
  for(const result of [multiply(identity,t),multiply(t,identity)]) assert(result.every((value,i)=>Math.abs(value-t[i])<1e-7));
});
test('camera projection is finite across mobile and desktop aspect ratios',()=>{
  for(const aspect of [.5,1,2,3]){const matrix=perspective(aspect);assert([...matrix].every(Number.isFinite));assert.equal(matrix[11],-1);assert(matrix[0]>0);}
});
test('every sample has its own coherent repository artifacts and check output',()=>{
  assert.equal(new Set(samples.map(s=>s.id)).size,samples.length);
  for(const sample of samples){assert.equal(sample.files.length,2);assert(sample.files.every(file=>file.endsWith('.py')&&!file.includes('/')));assert(sample.source.length);assert(sample.tests.length);assert(sample.failureOutput.startsWith('FAIL'));assert(sample.source.every(([kind,text])=>['add','remove','context'].includes(kind)&&typeof text==='string'));}
  assert.notDeepEqual(samples[0].source,samples[1].source);
});
