/* eslint-disable @typescript-eslint/no-explicit-any -- the server module is plain JavaScript */
// models.js against local HTTP servers standing in for a gateway and a proxy: the pages of a list, the
// places a list may live, a refused key, the credential from apiKeyHelper, custom headers, the cache, and
// Claude Code's proxy rules (HTTP_PROXY for http, CONNECT for https, NO_PROXY going around it).
import { afterAll, describe, expect, it, vi } from 'vitest';
import http from 'node:http';
import type { AddressInfo } from 'node:net';

const settings = vi.hoisted(() => ({ env: {} as Record<string, string>, apiKeyHelper: undefined as string | undefined }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ resolveSettings: async () => ({ effective: { env: settings.env, apiKeyHelper: settings.apiKeyHelper } }) }));
const { gatewayModels } = await import('../../server/models.js');

/** Claude Code's settings for one test: no proxy unless it says so (the machine's own must not leak in). */
const NO_PROXY_ENV = { HTTPS_PROXY: '', https_proxy: '', HTTP_PROXY: '', http_proxy: '', NO_PROXY: '', no_proxy: '' };
const use = (env: Record<string, string>, apiKeyHelper?: string) => {
  settings.env = { ...NO_PROXY_ENV, ...env };
  settings.apiKeyHelper = apiKeyHelper;
};

type Seen = { method: string; url: string; headers: http.IncomingHttpHeaders };
/** A server on a free port that answers with `answer(req)` and remembers what it was asked. */
async function server(answer: (req: http.IncomingMessage) => { status?: number; body?: unknown }) {
  const seen: Seen[] = [];
  const s = http.createServer((req, res) => {
    seen.push({ method: req.method!, url: req.url!, headers: req.headers });
    const { status = 200, body = {} } = answer(req);
    res.writeHead(status, { 'content-type': 'application/json' }).end(typeof body === 'string' ? body : JSON.stringify(body));
  });
  s.on('connect', (req, socket) => {
    seen.push({ method: 'CONNECT', url: req.url!, headers: req.headers });
    socket.end('HTTP/1.1 403 Forbidden\r\n\r\n');
  });
  await new Promise<void>((r) => s.listen(0, '127.0.0.1', r));
  servers.push(s);
  return { seen, url: `http://127.0.0.1:${(s.address() as AddressInfo).port}` };
}
const servers: http.Server[] = [];
afterAll(() => servers.forEach((s) => s.close()));

/** A gateway whose list comes in pages of one model. */
const pages = (models: object[]) => (req: http.IncomingMessage) => {
  const u = new URL(req.url!, 'http://x');
  if (u.pathname !== '/v1/models') return { status: 404 };
  const after = u.searchParams.get('after_id');
  const i = after ? models.findIndex((m: any) => m.id === after) + 1 : 0;
  return { body: { data: models.slice(i, i + 1), has_more: i + 1 < models.length, last_id: (models[i] as any)?.id } };
};

describe('the model list', () => {
  it('follows the pages, sends the credential and the custom headers, and puts Claude models first, newest first', async () => {
    const gw = await server(pages([{ id: 'gpt-x' }, { id: 'claude-old', created_at: '2025-01-01T00:00:00Z' }, { id: 'claude-new', display_name: 'Claude New', created_at: '2026-01-01T00:00:00Z' }]));
    use({ ANTHROPIC_BASE_URL: gw.url, ANTHROPIC_API_KEY: 'sk-test', ANTHROPIC_CUSTOM_HEADERS: 'X-Team: blue\nbroken line' });
    const r: any = await gatewayModels('/tmp', { version: '9.9.9' });
    expect(r).toMatchObject({ ok: true, url: `${gw.url}/v1/models` });
    expect(r.models.map((m: any) => m.id)).toEqual(['claude-new', 'claude-old', 'gpt-x']);
    expect(r.models[0].displayName).toBe('Claude New');
    expect(gw.seen.map((s) => new URL(s.url, 'http://x').searchParams.get('after_id'))).toEqual([null, 'gpt-x', 'claude-old']);
    expect(gw.seen[0].headers).toMatchObject({ 'x-api-key': 'sk-test', 'anthropic-version': '2023-06-01', 'x-team': 'blue', 'user-agent': 'pilcrow/9.9.9' });
  });

  it('is asked once in ten minutes for the same gateway and key, unless forced', async () => {
    const gw = await server(pages([{ id: 'claude-a' }]));
    use({ ANTHROPIC_BASE_URL: gw.url, ANTHROPIC_AUTH_TOKEN: 'tok' });
    await gatewayModels('/tmp');
    await gatewayModels('/tmp');
    expect(gw.seen).toHaveLength(1);
    expect(gw.seen[0].headers.authorization).toBe('Bearer tok');
    await gatewayModels('/tmp', { force: true });
    expect(gw.seen).toHaveLength(2);
  });

  it('looks at the host root for a gateway that keeps the Anthropic API under a path', async () => {
    const gw = await server(pages([{ id: 'claude-a' }]));
    use({ ANTHROPIC_BASE_URL: `${gw.url}/anthropic`, ANTHROPIC_API_KEY: 'k' });
    const r: any = await gatewayModels('/tmp');
    expect(r).toMatchObject({ ok: true, url: `${gw.url}/v1/models` });
    expect(gw.seen.map((s) => s.url.split('?')[0])).toEqual(['/anthropic/v1/models', '/v1/models']);
  });

  it('stops at a refused key, says when there is no credential, and takes one from apiKeyHelper', async () => {
    const refusing = await server(() => ({ status: 401 }));
    use({ ANTHROPIC_BASE_URL: `${refusing.url}/anthropic`, ANTHROPIC_API_KEY: 'bad' });
    expect(await gatewayModels('/tmp')).toMatchObject({ ok: false, error: 'The gateway refused the credential (HTTP 401)' });
    expect(refusing.seen).toHaveLength(1); // a refused key is not tried elsewhere
    use({ ANTHROPIC_BASE_URL: refusing.url });
    expect(await gatewayModels('/tmp')).toMatchObject({ ok: false, noCredential: true });
    const gw = await server(pages([{ id: 'claude-a' }]));
    use({ ANTHROPIC_BASE_URL: gw.url }, 'echo key-from-helper');
    expect(await gatewayModels('/tmp')).toMatchObject({ ok: true });
    expect(gw.seen[0].headers['x-api-key']).toBe('key-from-helper');
  });

  it('says a Bedrock or Vertex setup has no list', async () => {
    use({ CLAUDE_CODE_USE_BEDROCK: '1' });
    expect(await gatewayModels('/tmp')).toMatchObject({ ok: false, unsupported: true });
  });
});

describe("Claude Code's proxy rules", () => {
  it('sends an http request to HTTP_PROXY with the whole URL', async () => {
    const proxy = await server(pages([{ id: 'claude-via-proxy' }])); // the proxy answers for the gateway
    use({ ANTHROPIC_BASE_URL: 'http://gateway.invalid', ANTHROPIC_API_KEY: 'k', HTTP_PROXY: proxy.url });
    const r: any = await gatewayModels('/tmp', { force: true });
    expect(proxy.seen[0].url).toMatch(/^http:\/\/gateway\.invalid\/v1\/models\?limit=1000/);
    expect(proxy.seen[0].headers.host).toBe('gateway.invalid');
    expect(r).toMatchObject({ ok: true, models: [{ id: 'claude-via-proxy' }] });
  });

  it('opens a CONNECT tunnel for https through HTTPS_PROXY, and says when the proxy refuses it', async () => {
    const proxy = await server(() => ({}));
    use({ ANTHROPIC_BASE_URL: 'https://gateway.invalid', ANTHROPIC_API_KEY: 'k', HTTPS_PROXY: proxy.url });
    const r: any = await gatewayModels('/tmp', { force: true });
    expect(proxy.seen[0]).toMatchObject({ method: 'CONNECT', url: 'gateway.invalid:443' });
    expect(r).toMatchObject({ ok: false, error: 'The proxy refused the tunnel (HTTP 403)' });
  });

  it('goes around the proxy for a host in NO_PROXY', async () => {
    const gw = await server(pages([{ id: 'claude-direct' }]));
    use({ ANTHROPIC_BASE_URL: gw.url, ANTHROPIC_API_KEY: 'k', HTTP_PROXY: 'http://127.0.0.1:1', NO_PROXY: 'example.com, 127.0.0.1' });
    expect(await gatewayModels('/tmp', { force: true })).toMatchObject({ ok: true, models: [{ id: 'claude-direct' }] });
  });

  it('says it could not reach the proxy, naming it', async () => {
    use({ ANTHROPIC_BASE_URL: 'https://gateway.invalid', ANTHROPIC_API_KEY: 'k', HTTPS_PROXY: 'http://127.0.0.1:1' });
    expect((await gatewayModels('/tmp', { force: true })).error).toMatch(/^Could not reach the proxy 127\.0\.0\.1:1 \(HTTPS_PROXY\)/);
  });
});
