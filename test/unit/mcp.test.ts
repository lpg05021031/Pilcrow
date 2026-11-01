// customize.js's MCP half: the servers Claude Code's own files declare (user, local, a project's
// .mcp.json with this user's approval), the JSON built from the form, a project server approved or
// refused in ~/.claude.json, and adding or removing one through `claude mcp` (a fake one here).
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { addMcp, approveProjectMcp, listMcp, mcpConfigFromForm, removeMcp } from '../../server/customize.js';

const CLAUDE_JSON = path.join(process.env.CLAUDE_CONFIG_DIR!, '.claude.json');
const cwd = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-mcp-')));
fs.mkdirSync(path.dirname(CLAUDE_JSON), { recursive: true });
fs.writeFileSync(CLAUDE_JSON, JSON.stringify({ mcpServers: { docs: { url: 'https://docs.example/sse' } }, projects: { [cwd]: { mcpServers: { db: { command: 'db-mcp', args: ['--ro'] } } } } }));
fs.writeFileSync(path.join(cwd, '.mcp.json'), JSON.stringify({ mcpServers: { team: { type: 'http', url: 'https://team.example/mcp' } } }));

describe('MCP servers', () => {
  it('lists the user, local and project servers, a project one not yet approved', () => {
    const { servers } = listMcp({ cwd });
    expect(servers.map((s) => [s.name, s.scope, s.config.type, s.approved])).toEqual([
      ['docs', 'user', 'sse', undefined],
      ['db', 'local', 'stdio', undefined],
      ['team', 'project', 'http', null],
    ]);
    expect(listMcp({}).servers.map((s) => s.name)).toEqual(['docs']);
  });

  it('records an approval in ~/.claude.json, and a refusal replaces it', () => {
    expect(approveProjectMcp({ cwd, name: 'team', approved: true }).servers.find((s) => s.name === 'team')!.approved).toBe(true);
    expect(approveProjectMcp({ cwd, name: 'team', approved: false }).servers.find((s) => s.name === 'team')!.approved).toBe(false);
    const proj = JSON.parse(fs.readFileSync(CLAUDE_JSON, 'utf8')).projects[cwd];
    expect([proj.enabledMcpjsonServers, proj.disabledMcpjsonServers, Object.keys(proj.mcpServers)]).toEqual([[], ['team'], ['db']]);
  });

  it('builds the JSON from the form, and says what is missing', () => {
    expect(mcpConfigFromForm({ type: 'stdio', command: ' npx ', args: 'server --root "My Docs"', env: 'TOKEN=abc\n\nbad line\nMODE: ro' })).toEqual({ type: 'stdio', command: 'npx', args: ['server', '--root', 'My Docs'], env: { TOKEN: 'abc', MODE: 'ro' } });
    expect(mcpConfigFromForm({ url: 'https://x.example/mcp', headers: { Authorization: 'Bearer t' } })).toEqual({ type: 'http', url: 'https://x.example/mcp', headers: { Authorization: 'Bearer t' } });
    expect(() => mcpConfigFromForm({ type: 'stdio' })).toThrow('A command is required');
    expect(() => mcpConfigFromForm({ url: 'ftp://x' })).toThrow('http:// or https://');
  });

  it('adds and removes through `claude mcp`, and passes on what it says when it fails', async () => {
    const log = path.join(cwd, 'calls.txt');
    const bin = path.join(cwd, 'claude');
    fs.writeFileSync(bin, `#!/bin/sh\necho "$*" >> "${log}"\n[ "$2" = "remove" ] && [ "$4" = "user" ] && { echo "error: No MCP server named nope" >&2; exit 1; }\nexit 0\n`, { mode: 0o755 });
    await addMcp({ bin, scope: 'local', cwd, name: 'search', config: { type: 'http', url: 'https://s.example' } });
    await removeMcp({ bin, scope: 'local', cwd, name: 'search' });
    await expect(removeMcp({ bin, scope: 'user', cwd, name: 'nope' })).rejects.toThrow(/^No MCP server named nope$/);
    await expect(addMcp({ bin, name: 'bad name!', config: {} })).rejects.toThrow(/Server names/);
    await expect(addMcp({ bin, scope: 'project', name: 'p', config: {} })).rejects.toThrow(/open session folder/);
    expect(fs.readFileSync(log, 'utf8').trim().split('\n')).toEqual(['mcp add-json -s local search {"type":"http","url":"https://s.example"}', 'mcp remove -s local search', 'mcp remove -s user nope']);
  });
});
