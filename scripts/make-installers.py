#!/usr/bin/env python3
"""Turn the working tree into the three Pilcrow installer files.

    python scripts/make-installers.py [--out out] [--version X.Y.Z] [--gzip-mtime N]

Produces, in --out (default: out/):

    pilcrow-install.sh    Linux installer: every source file as a heredoc with its sha256,
                          the prebuilt interface (web/dist) as a base64 tgz split into chunks
    pilcrow-windows.ps1   Windows installer: the same files plus web/dist as one base64 zip,
                          and the WebView2 shell script (scripts/pilcrow-app.ps1) as base64
    pilcrow-app.sh        the Linux desktop shell, verbatim copy of scripts/pilcrow-app.sh

The fixed parts of both installers live in scripts/installer/*.tmpl; this script only fills
the {{PLACEHOLDERS}}. Needs web/dist (run `npm run build` first). Output is deterministic
for a given tree: timestamps inside the payloads are fixed, so the same tree gives the same
installers byte for byte (the gzip header mtime is 0 unless --gzip-mtime is given).
"""
import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, 'scripts', 'installer')

# What goes into the installers. A trailing slash means "every regular file below, recursively,
# skipping dotfiles". Build tools, tests and the installer machinery itself stay out.
INCLUDE = [
    '.gitignore',
    'assets/icon.svg',
    'CHANGELOG.md',
    'bin/',
    'electron/',
    'package-lock.json',
    'package.json',
    'README.md',
    'scripts/electron.sh',
    'scripts/install-desktop.sh',
    'scripts/make-icon.mjs',
    'scripts/pilcrow-app.sh',
    'server/',
    'tsconfig.json',
    'vite.config.mjs',
    'web/index.html',
    'web/public/',
    'web/src/',
    'qt/',
]
# Folders never shipped (tests, caches) and binary folders that travel as a base64 tgz in the Linux
# installer (heredocs carry text only) and as plain files in the Windows zip.
EXCLUDE_DIRS = {'qt/tests', 'qt/pilcrow_qt/__pycache__'}
EXCLUDE_NAMES = {'__pycache__', '.pytest_cache', '.ruff_cache'}
BUNDLE_DIRS = {'qt/pilcrow_qt/assets/fonts': 'qt/pilcrow_qt/assets/fonts-bundle'}
HOST_SCRIPT = 'scripts/pilcrow-app.ps1'
DIST = 'web/dist'
B64_COLS = 76
CHUNK_LINES = 1861           # 1861 lines x 77 bytes = 143297 bytes per chunk, as the originals
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def lower_key(p):
    return p.lower()


def read(path):
    with open(os.path.join(ROOT, path), 'rb') as f:
        return f.read()


def collect(spec):
    """Resolve the INCLUDE spec to {relative/posix/path: bytes}."""
    out = {}
    for item in spec:
        full = os.path.join(ROOT, item)
        if item.endswith('/'):
            if not os.path.isdir(full):
                sys.exit(f'missing directory: {item}')
            for dirpath, dirnames, filenames in os.walk(full):
                reld = os.path.relpath(dirpath, ROOT).replace(os.sep, '/')
                dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d not in EXCLUDE_NAMES and f'{reld}/{d}' not in EXCLUDE_DIRS and f'{reld}/{d}' not in BUNDLE_DIRS)
                for fn in sorted(filenames):
                    if fn.startswith('.'):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, '/')
                    out[rel] = read(rel)
        else:
            if not os.path.isfile(full):
                sys.exit(f'missing file: {item}')
            out[item] = read(item)
    return out


def collect_dist():
    full = os.path.join(ROOT, DIST)
    if not os.path.isfile(os.path.join(full, 'index.html')):
        sys.exit(f'{DIST}/index.html not found: run `npm run build` first')
    return collect_tree(DIST)


def collect_tree(rel_dir):
    """Every file below a folder, keyed by its path relative to that folder."""
    full = os.path.join(ROOT, rel_dir)
    out = {}
    for dirpath, dirnames, filenames in os.walk(full):
        dirnames.sort()
        for fn in sorted(filenames):
            rel = os.path.relpath(os.path.join(dirpath, fn), full).replace(os.sep, '/')
            with open(os.path.join(dirpath, fn), 'rb') as f:
                out[rel] = f.read()
    return out


def collect_bundles():
    """Binary folders (fonts) as {folder: {relative path: bytes}}; missing folders are skipped."""
    return {d: collect_tree(d) for d in BUNDLE_DIRS if os.path.isdir(os.path.join(ROOT, d))}


def b64_lines(data):
    s = base64.b64encode(data).decode('ascii')
    return [s[i:i + B64_COLS] for i in range(0, len(s), B64_COLS)]


# ---------------------------------------------------------------- Linux

def make_tgz(tree, prefix, gzip_mtime):
    """A deterministic tgz of {relative path: bytes} under ``prefix/`` (fixed metadata, sorted entries)."""
    buf = io.BytesIO()
    import gzip
    gz = gzip.GzipFile(filename='', mode='wb', compresslevel=9, fileobj=buf, mtime=gzip_mtime)
    with tarfile.open(fileobj=gz, mode='w') as tar:
        names = sorted({prefix} | {prefix + '/' + os.path.dirname(p) for p in tree if '/' in p} | {prefix + '/' + p for p in tree})
        for name in names:
            rel = name[len(prefix) + 1:] if name != prefix else None
            info = tarfile.TarInfo(name)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            if rel is None or rel not in tree:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                data = tree[rel]
                info.type = tarfile.REGTYPE
                info.mode = 0o644
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    gz.close()
    return buf.getvalue()


def chunked(tgz, out_dir, stem):
    """A tgz as 76-column base64 split into numbered chunk files, plus its sha256 file."""
    lines = b64_lines(tgz)
    files = {}
    for n, i in enumerate(range(0, len(lines), CHUNK_LINES), start=1):
        files[f'{out_dir}/{stem}.tgz.b64.{n:02d}'] = ('\n'.join(lines[i:i + CHUNK_LINES]) + '\n').encode('ascii')
    files[f'{out_dir}/{stem}.tgz.sha256'] = (sha256(tgz) + '\n').encode('ascii')
    return files


def ui_bundle(dist, gzip_mtime):
    """web/dist -> deterministic dist/ tgz -> {web/ui-bundle/...: bytes}."""
    tgz = make_tgz(dist, 'dist', gzip_mtime)
    return chunked(tgz, 'web/ui-bundle', 'ui'), tgz


def binary_bundles(bundles, gzip_mtime):
    """Each binary folder -> chunks under its bundle folder, unpacked by the installer's pilcrow_unpack."""
    files = {}
    for folder, tree in bundles.items():
        out_dir = BUNDLE_DIRS[folder]
        files.update(chunked(make_tgz(tree, os.path.basename(folder), gzip_mtime), out_dir, os.path.basename(folder)))
    return files


def heredoc(path, data):
    tag = 'PILCROW_EOF_' + sha256(path.encode('utf-8'))[:10]
    if not data.endswith(b'\n'):
        sys.exit(f'{path}: must end with a newline (heredoc files cannot carry a missing final newline)')
    if (tag + '\n').encode() in data:
        sys.exit(f'{path}: contains its own heredoc tag')
    if b'\r' in data:
        sys.exit(f'{path}: has CR line endings; the installer writes files as-is and bash would choke on them')
    return (f'  # ---- {path} ({len(data)} bytes)\n'
            f'  cat > "$D"/\'{path}\' <<\'{tag}\'\n').encode('utf-8') + data + f'{tag}\n  wrote=$((wrote + 1))\n'.encode('utf-8')


def linux_installer(version, files, gzip_mtime, bundles):
    dist = collect_dist()
    bundle, tgz = ui_bundle(dist, gzip_mtime)
    all_files = dict(files)
    all_files.update(bundle)
    all_files.update(binary_bundles(bundles, gzip_mtime))
    order = sorted(all_files, key=lower_key)
    dirs = sorted({os.path.dirname(p) for p in order if '/' in p})
    dirs = sorted({d for p in dirs for d in parents(p)})
    mkdir = '  mkdir -p ' + ' '.join(f'"$D"/\'{d}\'' for d in dirs) + '\n'
    write_all = mkdir.encode('utf-8') + b''.join(heredoc(p, all_files[p]) for p in order)
    write_all = write_all[:-1]  # the template supplies the newline before the closing brace
    tmpl = read('scripts/installer/linux.sh.tmpl')
    out = fill(tmpl, {
        'VERSION': version.encode(),
        'MANIFEST': '\n'.join(order).encode('utf-8'),
        'WRITE_ALL': write_all,
        'SUMS': '\n'.join(f'{sha256(all_files[p])}  {p}' for p in order).encode('utf-8'),
    })
    return out, tgz, dist


def parents(d):
    parts = d.split('/')
    return ['/'.join(parts[:i]) for i in range(1, len(parts) + 1)]


# ---------------------------------------------------------------- Windows

def windows_installer(version, files, dist, bundles):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        entries = [(p, files[p]) for p in sorted(files, key=lower_key)]
        entries += [('web/dist/' + p, dist[p]) for p in sorted(dist)]
        for folder, tree in sorted(bundles.items()):
            entries += [(folder + '/' + p, tree[p]) for p in sorted(tree)]
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, data)
    zb = buf.getvalue()
    host = read(HOST_SCRIPT)
    tmpl = read('scripts/installer/windows.ps1.tmpl')
    return fill(tmpl, {
        'VERSION': version.encode(),
        'PAYLOAD': '\n'.join(b64_lines(zb)).encode('ascii'),
        'DIGEST': sha256(zb).encode(),
        'HOST_PAYLOAD': '\n'.join(b64_lines(host)).encode('ascii'),
    }), zb


def fill(tmpl, values):
    import re
    wanted = set(m.decode() for m in re.findall(rb'\{\{([A-Z_]+)\}\}', tmpl))
    if wanted != set(values):
        sys.exit(f'template placeholders {sorted(wanted)} do not match values {sorted(values)}')
    out = tmpl
    for k, v in values.items():
        out = out.replace(('{{' + k + '}}').encode(), v)
    return out


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(ROOT, 'out'))
    ap.add_argument('--version', default=None, help='default: package.json')
    ap.add_argument('--gzip-mtime', type=int, default=0, help='mtime in the gzip header of the UI bundle (default 0)')
    ap.add_argument('--verify', action='store_true', help='afterwards, unpack the Linux installer and compare it with the tree')
    args = ap.parse_args()

    version = args.version or json.loads(read('package.json'))['version']
    pkg_version = json.loads(read('package.json'))['version']
    if version != pkg_version:
        print(f'warning: --version {version} differs from package.json {pkg_version}', file=sys.stderr)

    files = collect(INCLUDE)
    bundles = collect_bundles()
    sh, tgz, dist = linux_installer(version, files, args.gzip_mtime, bundles)
    ps1, zb = windows_installer(version, files, dist, bundles)
    app_sh = files['scripts/pilcrow-app.sh']

    os.makedirs(args.out, exist_ok=True)
    for name, data in (('pilcrow-install.sh', sh), ('pilcrow-windows.ps1', ps1), ('pilcrow-app.sh', app_sh)):
        path = os.path.join(args.out, name)
        with open(path, 'wb') as f:
            f.write(data)
        if name.endswith('.sh'):
            try:
                os.chmod(path, 0o755)
            except OSError:
                pass
        print(f'{name}: {len(data):,} bytes')
    print(f'  {len(files)} source files, {len(dist)} interface files; UI bundle {len(tgz):,} bytes, zip {len(zb):,} bytes')
    if args.verify:
        verify(os.path.join(args.out, 'pilcrow-install.sh'), os.path.join(args.out, 'pilcrow-windows.ps1'), files, dist, read(HOST_SCRIPT), bundles)


def verify(sh_path, ps1_path, files, dist, host, bundles):
    """Parse the installers back and check every file against the tree."""
    import re
    with open(sh_path, 'rb') as f:
        sh = f.read()
    pat = re.compile(rb"  # ---- (?P<path>[^\n]+?) \((?P<size>\d+) bytes\)\n"
                     rb"  cat > \"\$D\"/'(?P=path)' <<'(?P<eof>PILCROW_EOF_[0-9a-f]+)'\n"
                     rb"(?P<body>.*?)(?P=eof)\n", re.S)
    got = {}
    for m in pat.finditer(sh):
        p = m.group('path').decode('utf-8')
        body = m.group('body')
        if len(body) != int(m.group('size')):
            sys.exit(f'verify: {p}: size mismatch in the Linux installer')
        got[p] = body
    sums_text = re.search(rb"PILCROW_SUMS_EOF'\n(.*?)PILCROW_SUMS_EOF", sh, re.S).group(1).decode().strip()
    sums = dict(l.split(maxsplit=1)[::-1] for l in sums_text.split('\n'))
    if set(sums) != set(got) or any(sums[p] != sha256(got[p]) for p in got):
        sys.exit('verify: the sha256 list in the Linux installer does not match its files')
    chunks = sorted(p for p in got if p.startswith('web/ui-bundle/ui.tgz.b64.'))
    tgz = base64.b64decode(b''.join(got[c] for c in chunks))
    if sha256(tgz) != got['web/ui-bundle/ui.tgz.sha256'].decode().split()[0]:
        sys.exit('verify: UI bundle checksum mismatch')
    got_dist = {}
    with tarfile.open(fileobj=io.BytesIO(tgz), mode='r:gz') as t:
        for mem in t.getmembers():
            if mem.isfile():
                got_dist[mem.name[len('dist/'):]] = t.extractfile(mem).read()
    bundle_dirs = set(BUNDLE_DIRS.values())
    src = {p: b for p, b in got.items() if not p.startswith('web/ui-bundle/') and not any(p.startswith(d + '/') for d in bundle_dirs)}
    problems = [p for p in set(src) | set(files) if src.get(p) != files.get(p)]
    problems += ['web/dist/' + p for p in set(got_dist) | set(dist) if got_dist.get(p) != dist.get(p)]
    for folder, tree in bundles.items():
        out_dir = BUNDLE_DIRS[folder]
        stem = os.path.basename(folder)
        parts = sorted(p for p in got if p.startswith(f'{out_dir}/{stem}.tgz.b64.'))
        btgz = base64.b64decode(b''.join(got[c] for c in parts))
        if sha256(btgz) != got[f'{out_dir}/{stem}.tgz.sha256'].decode().split()[0]:
            sys.exit(f'verify: {folder} bundle checksum mismatch')
        got_tree = {}
        with tarfile.open(fileobj=io.BytesIO(btgz), mode='r:gz') as t:
            for mem in t.getmembers():
                if mem.isfile():
                    got_tree[mem.name[len(stem) + 1:]] = t.extractfile(mem).read()
        problems += [folder + '/' + p for p in set(got_tree) | set(tree) if got_tree.get(p) != tree.get(p)]
    if problems:
        sys.exit('verify: Linux installer differs from the tree: ' + ', '.join(sorted(problems)[:10]))
    with open(ps1_path, 'rb') as f:
        ps1 = f.read()
    zb = base64.b64decode(re.sub(rb'\s', b'', re.search(rb"\$payload = @'\n(.*?)\n'@", ps1, re.S).group(1)))
    if sha256(zb) != re.search(rb"\$digest -ne '([0-9a-f]{64})'", ps1).group(1).decode():
        sys.exit('verify: Windows payload digest mismatch')
    z = zipfile.ZipFile(io.BytesIO(zb))
    zf = {i.filename: z.read(i) for i in z.infolist() if not i.filename.endswith('/')}
    want = dict(files)
    want.update({'web/dist/' + p: b for p, b in dist.items()})
    for folder, tree in bundles.items():
        want.update({folder + '/' + p: b for p, b in tree.items()})
    problems = [p for p in set(zf) | set(want) if zf.get(p) != want.get(p)]
    if problems:
        sys.exit('verify: Windows installer differs from the tree: ' + ', '.join(sorted(problems)[:10]))
    hp = base64.b64decode(re.sub(rb'\s', b'', re.search(rb"\$HostPayload = @'\n(.*?)\n'@", ps1, re.S).group(1)))
    if hp != host:
        sys.exit('verify: Windows shell script payload differs from ' + HOST_SCRIPT)
    print(f'verify: OK ({len(src)} source files, {len(got_dist)} interface files, both installers match the tree)')


if __name__ == '__main__':
    main()
