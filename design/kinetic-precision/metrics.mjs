import { readFile, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { createSlab } from './geometry.js';

const runtimeFiles=['index.html','styles.css','app.js','samples.js','engine.js','geometry.js','mark.svg','assets/bricolage.woff2','assets/plex-sans.woff2','assets/plex-mono.woff2'];
const files=[];
for(const name of runtimeFiles){const buffer=await readFile(new URL(name,import.meta.url));files.push({name,bytes:buffer.length,gzipBytes:gzipSync(buffer).length});}
const mesh=createSlab();
const report={files,rawRuntimeBytes:files.reduce((s,f)=>s+f.bytes,0),gzipRuntimeBytes:files.reduce((s,f)=>s+f.gzipBytes,0),meshBufferBytes:mesh.byteLength,trianglesPerLayer:mesh.length/18,drawCallsPerFrame:5,maxPixelRatio:1.6,notes:'Source-size measurements, not a hardware performance benchmark. Python preview serves uncompressed files. Fonts are already WOFF2-compressed. Original source TTF is not loaded.'};
await writeFile(new URL('../../.tmp/kinetic-evidence/metrics.json',import.meta.url),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
