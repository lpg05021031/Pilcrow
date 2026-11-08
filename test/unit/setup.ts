// Every unit test file runs with a home of its own: the server modules keep their files under HOME and
// the XDG folders (and Claude Code's under CLAUDE_CONFIG_DIR), so a test that imports them, or starts
// the server, never reads or writes the developer's real ones. Vitest runs this before each file's imports.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'pilcrow-home-'));
process.env.HOME = home;
process.env.USERPROFILE = home;
process.env.XDG_CONFIG_HOME = path.join(home, '.config');
process.env.XDG_DATA_HOME = path.join(home, '.local', 'share');
process.env.XDG_STATE_HOME = path.join(home, '.local', 'state');
process.env.CLAUDE_CONFIG_DIR = path.join(home, '.claude');
// Commits made by the code under test need an identity, and must not depend on the developer's signing setup.
fs.writeFileSync(path.join(home, '.gitconfig'), '[user]\n\tname = Pilcrow Test\n\temail = test@pilcrow.invalid\n[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n');
