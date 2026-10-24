const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../portal/static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../portal/static/index.html'),'utf8');
const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]));
const nodes=new Map();
function node(){return {hidden:true,textContent:'',className:'',style:{},classList:{toggle(){}},append(){},replaceChildren(){},addEventListener(){}};}
const context=vm.createContext({document:{hidden:false,getElementById(id){assert(ids.has(id),`Missing DOM element: ${id}`);if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},querySelectorAll(){return [];},createElement:node,createTextNode:node},fetch:async()=>({ok:false,status:401,json:async()=>({error:'Login required'})}),setInterval(){},setTimeout(){},Date,Math,Number,String,Error,console});
vm.runInContext(source,context);
assert(!html.includes('id="tab-search"'));
assert(!html.includes('id="tab-library"'));
assert(html.includes('http://localhost:5055'));
assert(html.includes('http://localhost:8096'));
async function check(){
  context.fetch=async()=>({ok:false,status:502,json:async()=>{throw new SyntaxError('private HTML must not leak');}});
  await assert.rejects(vm.runInContext("api('/api/status')",context),error=>error.message.includes('HTTP 502')&&!error.message.includes('private HTML'));
  context.fetch=async()=>{throw new TypeError('network detail');};
  await assert.rejects(vm.runInContext("api('/api/status')",context),error=>error.message.includes('Sem conexão'));
  context.fetch=async()=>({ok:true,status:200,json:async()=>({ok:true})});
  assert.equal((await vm.runInContext("api('/api/status')",context)).ok,true);
  console.log('PASS: lean hub DOM, native-app links and friendly HTML/network failures.');
}
check().catch(error=>{console.error(error);process.exitCode=1;});
