#!/usr/bin/env node
/** Lumen V32 Railway edge: control-plane proxy + registered Xray transports. */
'use strict';
const http=require('http');const net=require('net');const fs=require('fs');
const PUBLIC_PORT=Number(process.env.PORT||8080);const CONTROL_PORT=Number(process.env.LUMEN_CONTROL_PORT||8001);
const ROUTES_FILE=process.env.LUMEN_XRAY_ROUTES||'/data/xray-routes.json';
let routes=new Map(),mtime=0;const activeIps=new Map();
function loadRoutes(){try{const st=fs.statSync(ROUTES_FILE);if(st.mtimeMs===mtime)return;const d=JSON.parse(fs.readFileSync(ROUTES_FILE,'utf8'));const next=new Map();for(const r of d.routes||[]){if(r&&typeof r.path==='string'&&Number.isInteger(r.port))next.set(r.path,r)}routes=next;mtime=st.mtimeMs;}catch(e){if(e.code!=='ENOENT')console.error('route map:',e.message)}}
setInterval(loadRoutes,500).unref();loadRoutes();
function pathOf(url){try{return new URL(url||'/','http://lumen.invalid').pathname}catch{return '/'}}
function clientIp(req){const railway=req.headers['x-forwarded-for'];return typeof railway==='string'&&railway?railway.split(',').at(-1).trim():(req.socket.remoteAddress||'')}
function ipAllowed(route,req){const limit=Number(route.ip_limit||0);if(!limit)return true;const key=route.uuid,ip=clientIp(req),now=Date.now();let map=activeIps.get(key);if(!map){map=new Map();activeIps.set(key,map)}for(const [addr,seen] of map)if(now-seen>120000)map.delete(addr);if(!map.has(ip)&&map.size>=limit)return false;map.set(ip,now);return true}
function proxyControl(req,res){const upstream=http.request({host:'127.0.0.1',port:CONTROL_PORT,path:req.url,method:req.method,headers:{...req.headers,host:req.headers.host||'localhost'}},r=>{res.writeHead(r.statusCode||502,r.headers);r.pipe(res)});upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end('Lumen control plane unavailable')});req.pipe(upstream)}
function proxyHttp(req,res,route){if(!ipAllowed(route,req)){res.writeHead(403);res.end();return}const headers={...req.headers,host:`127.0.0.1:${route.port}`,'x-forwarded-for':clientIp(req)};const upstream=http.request({host:'127.0.0.1',port:route.port,path:req.url,method:req.method,headers},r=>{res.writeHead(r.statusCode||502,r.headers);r.pipe(res)});upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end()});req.pipe(upstream)}
const server=http.createServer((req,res)=>{loadRoutes();const route=routes.get(pathOf(req.url));if(route&&route.network==='xhttp')return proxyHttp(req,res,route);return proxyControl(req,res)});
server.on('upgrade',(req,socket,head)=>{loadRoutes();const route=routes.get(pathOf(req.url));if(!route||!['ws','httpupgrade'].includes(route.network)||!ipAllowed(route,req)){socket.destroy();return}const upstream=net.connect(route.port,'127.0.0.1',()=>{const lines=[`${req.method} ${req.url} HTTP/${req.httpVersion}`];for(let i=0;i<req.rawHeaders.length;i+=2)lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i+1]}`);lines.push('\r\n');upstream.write(lines.join('\r\n'));if(head.length)upstream.write(head);socket.pipe(upstream);upstream.pipe(socket)});const close=()=>{socket.destroy();upstream.destroy()};socket.on('error',close);upstream.on('error',close)});
server.listen(PUBLIC_PORT,'0.0.0.0',()=>console.log(`Lumen V32 edge listening on ${PUBLIC_PORT}`));
for(const sig of ['SIGTERM','SIGINT'])process.on(sig,()=>server.close(()=>process.exit(0)));
