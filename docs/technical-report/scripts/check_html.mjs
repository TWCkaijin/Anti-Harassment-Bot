import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const root=path.resolve(import.meta.dirname,'..');
const output=path.resolve(process.env.DOC_EVIDENCE_DIR || '/tmp/rag-delivery-html');
await fs.mkdir(output,{recursive:true});
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const results=[];
try {
 for(const width of [1280,390]) {
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route(/^https?:/,route=>route.abort());
  for(const file of ['技術報告.html','manuals/使用者手冊.html','manuals/管理者手冊.html','manuals/開發維運手冊.html']) {
   const page=await context.newPage(), errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.goto(pathToFileURL(path.join(root,file)).href);
   const actual=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,title:document.title,headings:document.querySelectorAll('h2').length}));
   assert(actual.scrollWidth<=actual.width+1,`${file} horizontal overflow at ${width}`);
   assert.equal(errors.length,0);
   const folder=path.join(output,'screenshots');await fs.mkdir(folder,{recursive:true});
   await page.screenshot({path:path.join(folder,`${path.basename(file,'.html')}-${width}.png`)});
   const content_sha256=createHash('sha256').update(await fs.readFile(path.join(root,file))).digest('hex');
   results.push({file,viewport:width,...actual,errors,content_sha256});await page.close();
  }
  await context.close();
 }


 await fs.writeFile(path.join(output,'html-check.json'),JSON.stringify({offline:true,results},null,2));
 console.log(JSON.stringify({offline:true,checked:results.length}));
} finally {await browser.close();}
