// Lists the models a gateway offers (GET /v1/models), using the same settings Claude Code uses:
// ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / apiKeyHelper, ANTHROPIC_CUSTOM_HEADERS,
// HTTPS_PROXY / NO_PROXY and NODE_EXTRA_CA_CERTS, whether they come from settings.json or the environment.
// Works with Anthropic-style lists ({ data, has_more, last_id }) and OpenAI-style ones ({ data: [{ id }] }).
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import tls from 'node:tls';
import { resolveSettings } from '@anthropic-ai/claude-agent-sdk';
import { run, log } from './util.js';

const TTL = 10 * 60_000;
const TIMEOUT = 10_000;
let cache = null; // { key, at, result }
let inflight = null;

/**
 * Where a gateway's list may live. ANTHROPIC_BASE_URL is the root Claude Code appends /v1/messages to, so the
 * list is normally <base>/v1/models; some gateways put the Anthropic API under a path (https://gw/anthropic)
 * and keep one shared list at the host root, or are configured with a base that already ends in /v1.
 */
function candidates(base) {
  const out = [`${base}/v1/models`];
  if (/\/v1$/.test(base)) out.push(`${base}/models`);
  try {
    const u = new URL(base);
    if (u.pathname.replace(/\/+$/, '')) out.push(`${u.origin}/v1/models`);
  } catch {
    /* invalid base: the first attempt reports it */
  }
  return [...new Set(out)];
}

export async function gatewayModels(cwd, { force = false, version = '0' } = {}) {
  let settings;
  try {
    settings = await resolveSettings({ cwd: cwd || '/' });
  } catch (e) {
    return { ok: false, error: `Could not read Claude Code settings: ${e.message}` };
  }
  const env = { ...process.env, ...(settings.effective?.env || {}) };
  if (env.CLAUDE_CODE_USE_BEDROCK || env.CLAUDE_CODE_USE_VERTEX || env.CLAUDE_CODE_USE_FOUNDRY) {
    return { ok: false, unsupported: true, error: 'Model lists are only available for Anthropic-compatible endpoints' };
  }
  const base = (env.ANTHROPIC_BASE_URL || 'https://api.anthropic.com').replace(/\/+$/, '');
  const key = JSON.stringify([base, env.ANTHROPIC_AUTH_TOKEN || '', env.ANTHROPIC_API_KEY || '', settings.effective?.apiKeyHelper || '', env.ANTHROPIC_CUSTOM_HEADERS || '']);
  if (!force && cache && cache.key === key && Date.now() - cache.at < TTL) return cache.result;
  if (inflight && inflight.key === key) return inflight.promise;

  const promise = (async () => {
    const headers = { 'anthropic-version': '2023-06-01', accept: 'application/json', 'user-agent': `pilcrow/${version}` };
    let credential = false;
    if (env.ANTHROPIC_AUTH_TOKEN) {
      headers.authorization = `Bearer ${env.ANTHROPIC_AUTH_TOKEN}`;
      credential = true;
    }
    if (env.ANTHROPIC_API_KEY) {
      headers['x-api-key'] = env.ANTHROPIC_API_KEY;
      credential = true;
    }
    const helper = settings.effective?.apiKeyHelper;
    if (!credential && helper) {
      const r = process.platform === 'win32' ? await run(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', helper], { timeout: 15_000, env, windowsHide: true }) : await run('/bin/sh', ['-c', helper], { timeout: 15_000, env });
      const k = (r.stdout || '').trim();
      if (r.code !== 0 || !k) return { ok: false, base, error: 'apiKeyHelper did not return a key' };
      headers['x-api-key'] = k;
      headers.authorization = `Bearer ${k}`;
      credential = true;
    }
    if (!credential) return { ok: false, base, noCredential: true, error: 'No API key or token is configured for Claude Code' };
    for (const line of String(env.ANTHROPIC_CUSTOM_HEADERS || '').split(/\r?\n/)) {
      const i = line.indexOf(':');
      if (i > 0) headers[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
    }

    const models = [];
    let used = null;
    let lastError = null;
    for (const listUrl of candidates(base)) {
      const r = await fetchList(listUrl, headers, env);
      if (r.ok) {
        models.push(...r.models);
        used = listUrl;
        break;
      }
      lastError = r;
      // Only "not here" is worth trying elsewhere; a refused key or a network failure will not change.
      if (!r.notHere) break;
    }
    if (!used) return { base, ...lastError, ok: false };
    // Claude models first (newest first when the gateway says when they were released), then the rest by name.
    const claude = (id) => (/claude/i.test(id) ? 0 : 1);
    const family = (id) => (/opus/i.test(id) ? 0 : /sonnet/i.test(id) ? 1 : /haiku/i.test(id) ? 2 : 3);
    models.sort((a, b) => claude(a.id) - claude(b.id) || (b.created || 0) - (a.created || 0) || family(a.id) - family(b.id) || a.id.localeCompare(b.id));
    return { ok: true, base, url: used, models, fetchedAt: Date.now() };
  })();

  inflight = { key, promise };
  try {
    const result = await promise;
    if (result.ok) cache = { key, at: Date.now(), result };
    else log('gateway models:', result.error);
    return result;
  } finally {
    inflight = null;
  }
}

/** One list URL, following has_more pages. */
async function fetchList(listUrl, headers, env) {
  const models = [];
  const seen = new Set();
  let after = null;
  for (let page = 0; page < 20; page++) {
    let url;
    try {
      url = new URL(listUrl);
    } catch {
      return { ok: false, error: `Not a valid URL: ${listUrl}` };
    }
    if (!url.searchParams.has('limit')) url.searchParams.set('limit', '1000');
    if (after) url.searchParams.set('after_id', after);
    let res;
    try {
      res = await request(url, headers, env);
    } catch (e) {
      return { ok: false, error: networkMessage(e) };
    }
    if (res.status === 401 || res.status === 403) return { ok: false, error: `The gateway refused the credential (HTTP ${res.status})` };
    if (res.status === 404 || res.status === 405) return { ok: false, notHere: true, unsupported: true, error: `No model list at ${listUrl}` };
    if (res.status >= 400) return { ok: false, error: `The gateway answered HTTP ${res.status}` };
    let doc;
    try {
      doc = JSON.parse(res.body);
    } catch {
      return { ok: false, notHere: true, error: `${listUrl} did not answer with JSON` };
    }
    const list = Array.isArray(doc) ? doc : Array.isArray(doc?.data) ? doc.data : Array.isArray(doc?.models) ? doc.models : null;
    if (!list) return { ok: false, notHere: true, error: `Unrecognised model list format at ${listUrl}` };
    for (const m of list) {
      const id = typeof m === 'string' ? m : m?.id || m?.name || m?.model;
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const created = m?.created_at ? Date.parse(m.created_at) : typeof m?.created === 'number' ? m.created * 1000 : null;
      models.push({ id, displayName: m?.display_name || m?.displayName || null, created: Number.isFinite(created) ? created : null });
    }
    if (doc?.has_more && doc?.last_id && doc.last_id !== after) after = doc.last_id;
    else break;
  }
  return { ok: true, models };
}

function networkMessage(e) {
  const code = e?.code || e?.cause?.code;
  if (e?.proxy) return `Could not reach the proxy ${e.proxy} (HTTPS_PROXY)${code ? ` — ${code}` : ''}`;
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return 'Could not resolve the gateway host name';
  if (code === 'ECONNREFUSED') return 'The gateway refused the connection';
  if (code === 'ETIMEDOUT' || e?.message === 'timeout') return 'The gateway did not answer in time';
  if (/CERT|SELF_SIGNED|UNABLE_TO/.test(code || '')) return `TLS certificate problem (${code}); set NODE_EXTRA_CA_CERTS to your company CA`;
  return e?.message || String(e);
}

// ------------------------------------------------------------------ HTTP with Claude Code's proxy and CA rules

function extraCa(env) {
  const file = env.NODE_EXTRA_CA_CERTS;
  if (!file || file === process.env.NODE_EXTRA_CA_CERTS) return undefined; // Node already loaded the process one
  try {
    return [...tls.rootCertificates, fs.readFileSync(file, 'utf8')];
  } catch {
    return undefined;
  }
}

function proxyFor(url, env) {
  const noProxy = String(env.NO_PROXY || env.no_proxy || '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  const host = url.hostname.toLowerCase();
  const bypass = noProxy.some((p) => p === '*' || host === p.replace(/^\./, '') || host.endsWith(p.startsWith('.') ? p : `.${p}`) || (p.includes(':') && `${host}:${url.port}` === p));
  if (bypass) return null;
  const p = url.protocol === 'https:' ? env.HTTPS_PROXY || env.https_proxy || env.HTTP_PROXY || env.http_proxy : env.HTTP_PROXY || env.http_proxy;
  try {
    return p ? new URL(p) : null;
  } catch {
    return null;
  }
}

function request(url, headers, env) {
  const ca = extraCa(env);
  const rejectUnauthorized = env.NODE_TLS_REJECT_UNAUTHORIZED !== '0';
  const proxy = proxyFor(url, env);
  return new Promise((resolve, reject) => {
    const done = (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => {
        body += c;
        if (body.length > 20e6) res.destroy(new Error('response too large'));
      });
      res.on('end', () => resolve({ status: res.statusCode, body }));
      res.on('error', reject);
    };
    const fail = (e) => reject(e);
    const port = Number(url.port) || (url.protocol === 'https:' ? 443 : 80);
    const auth = (p) => (p.username ? { 'proxy-authorization': `Basic ${Buffer.from(`${decodeURIComponent(p.username)}:${decodeURIComponent(p.password)}`).toString('base64')}` } : {});

    if (!proxy) {
      const mod = url.protocol === 'https:' ? https : http;
      const req = mod.request(url, { method: 'GET', headers, ca, rejectUnauthorized, timeout: TIMEOUT }, done);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.on('error', fail);
      return req.end();
    }
    if (url.protocol === 'http:') {
      const req = http.request({ host: proxy.hostname, port: proxy.port || 80, method: 'GET', path: url.href, headers: { ...headers, host: url.host, ...auth(proxy) }, timeout: TIMEOUT }, done);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.on('error', fail);
      return req.end();
    }
    // HTTPS through the proxy: CONNECT, then TLS inside the tunnel.
    const pmod = proxy.protocol === 'https:' ? https : http;
    const connect = pmod.request({ host: proxy.hostname, port: proxy.port || (proxy.protocol === 'https:' ? 443 : 80), method: 'CONNECT', path: `${url.hostname}:${port}`, headers: { host: `${url.hostname}:${port}`, ...auth(proxy) }, timeout: TIMEOUT });
    connect.on('timeout', () => connect.destroy(new Error('timeout')));
    connect.on('error', (e) => {
      e.proxy = proxy.host;
      fail(e);
    });
    connect.on('connect', (res, socket) => {
      if (res.statusCode !== 200) {
        socket.destroy();
        return reject(new Error(`The proxy refused the tunnel (HTTP ${res.statusCode})`));
      }
      const req = https.request(url, { method: 'GET', headers, ca, rejectUnauthorized, timeout: TIMEOUT, createConnection: () => tls.connect({ socket, servername: url.hostname, ca, rejectUnauthorized }) }, done);
      req.on('timeout', () => req.destroy(new Error('timeout')));
      req.on('error', fail);
      req.end();
    });
    connect.end();
  });
}
