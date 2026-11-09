#!/usr/bin/env python3
"""Turn the difference between two Pilcrow versions into one Linux update file.

    python scripts/make-patch.py [--from REF] [--out out] [--verify]

Produces, in --out (default: out/):

    pilcrow-patch.sh      Linux update: only the shipped files that changed between REF and the working
                          tree, each as a heredoc with its sha256, files REF had that are gone now, and
                          the prebuilt interface as a base64 tgz when it changed. Applied on top of an
                          existing install; the full pilcrow-install.sh stays the way to install fresh.

REF defaults to the most recent "Release X.Y.Z" commit whose version differs from package.json, so the
patch covers "the previous release up to now". What counts as shipped is the INCLUDE list of
make-installers.py, which this script imports; tests and the installer machinery stay out.

The interface comes from web/dist when it is built (run `npm run build` first); otherwise, the
bundle is lifted out of the pilcrow-install.sh at the repo root, provided its version matches, so a
patch made from a release commit carries byte for byte the interface that release shipped.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, 'scripts', 'installer', 'patch.sh.tmpl')
INSTALLER = os.path.join(ROOT, 'pilcrow-install.sh')

spec = importlib.util.spec_from_file_location('make_installers', os.path.join(ROOT, 'scripts', 'make-installers.py'))
mi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mi)


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True).stdout


def default_base(version):
    """The newest release commit that is not this version."""
    for line in git('log', '--format=%H%x09%s').decode('utf-8', 'replace').splitlines():
        h, _, subject = line.partition('\t')
        m = re.match(r'^Release (\d+\.\d+\.\d+)$', subject.strip())
        if m and m.group(1) != version:
            return h, m.group(1)
    sys.exit('no earlier "Release X.Y.Z" commit found; pass --from REF')


def base_version(ref):
    try:
        return json.loads(git('show', f'{ref}:package.json'))['version']
    except subprocess.CalledProcessError:
        sys.exit(f'{ref}: no package.json there')


def shipped(path):
    """Whether make-installers.py would put this path into the installers."""
    parts = path.split('/')
    if any(p.startswith('.') for p in parts[1:]) or any(p in mi.EXCLUDE_NAMES for p in parts):
        return False
    for item in mi.INCLUDE:
        if item.endswith('/'):
            if path.startswith(item):
                d = os.path.dirname(path)
                if any(d == x or d.startswith(x + '/') for x in mi.EXCLUDE_DIRS | set(mi.BUNDLE_DIRS)):
                    return False
                return True
        elif path == item:
            return True
    return False


def changed_files(base):
    """({path: bytes} written, [paths] removed) between the base commit and the working tree, shipped files only."""
    out = git('diff', '--name-status', '--no-renames', '-z', base, '--').decode('utf-8')
    fields = out.split('\0')
    written, removed = {}, []
    i = 0
    while i + 1 < len(fields):
        status, path = fields[i], fields[i + 1]
        i += 2
        if not path or not shipped(path):
            continue
        if status.startswith('D'):
            removed.append(path)
        else:
            written[path] = mi.read(path)
    return written, sorted(removed)


def deps(pkg):
    return {k: pkg.get(k, {}) for k in ('dependencies', 'optionalDependencies')}


def installer_bundle(version):
    """The web/ui-bundle/* files inside pilcrow-install.sh at the root, if it is this version."""
    if not os.path.isfile(INSTALLER):
        return None
    with open(INSTALLER, 'rb') as f:
        sh = f.read()
    m = re.search(rb'^PILCROW_VERSION=(\S+)$', sh, re.M)
    if not m or m.group(1).decode() != version:
        return None
    pat = re.compile(rb"  # ---- (?P<path>web/ui-bundle/[^\n]+?) \((?P<size>\d+) bytes\)\n"
                     rb"  cat > \"\$D\"/'(?P=path)' <<'(?P<eof>PILCROW_EOF_[0-9a-f]+)'\n"
                     rb"(?P<body>.*?)(?P=eof)\n", re.S)
    files = {}
    for mm in pat.finditer(sh):
        if len(mm.group('body')) != int(mm.group('size')):
            sys.exit('pilcrow-install.sh: a ui-bundle chunk has the wrong size')
        files[mm.group('path').decode()] = mm.group('body')
    return files or None


def bundle_sha(files):
    return files['web/ui-bundle/ui.tgz.sha256'].decode().split()[0]


def base_bundle_sha(base):
    """The interface checksum the base release shipped, from its installer's sha256 list."""
    try:
        sh = git('show', f'{base}:pilcrow-install.sh')
    except subprocess.CalledProcessError:
        return None
    m = re.search(rb'^([0-9a-f]{64})  web/ui-bundle/ui\.tgz\.sha256$', sh, re.M)
    if not m:
        return None
    # that line checks the sha256 *file*; the bundle's own digest is the file's content
    mm = re.search(rb"cat > \"\$D\"/'web/ui-bundle/ui\.tgz\.sha256' <<'(PILCROW_EOF_[0-9a-f]+)'\n([0-9a-f]{64})\n", sh)
    return mm.group(2).decode() if mm else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--from', dest='base', default=None, help='git ref of the version to patch from (default: the previous "Release X.Y.Z" commit)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out'))
    ap.add_argument('--gzip-mtime', type=int, default=0, help='mtime in the gzip header of a freshly built UI bundle (default 0)')
    ap.add_argument('--verify', action='store_true', help='afterwards, parse the patch back and compare it with the tree')
    args = ap.parse_args()

    pkg = json.loads(mi.read('package.json'))
    version = pkg['version']
    if args.base:
        base = git('rev-parse', '--verify', args.base + '^{commit}').decode().strip()
        base_ver = base_version(base)
    else:
        base, base_ver = default_base(version)
    if base_ver == version:
        print(f'warning: {base[:10]} is also {version}; the patch will say {version} -> {version}', file=sys.stderr)
    print(f'patch {base_ver} ({base[:10]}) -> {version} (working tree)')

    written, removed = changed_files(base)
    if not written and not removed:
        sys.exit('nothing shipped changed since the base: no patch to make')
    npm = deps(json.loads(git('show', f'{base}:package.json'))) != deps(pkg)

    # ---- interface
    ui_files = {}
    dist_index = os.path.join(ROOT, mi.DIST, 'index.html')
    if os.path.isfile(dist_index):
        dist = mi.collect_dist()
        ui_files, tgz = mi.ui_bundle(dist, args.gzip_mtime)
        source = f'web/dist ({len(dist)} files)'
    else:
        ui_files = installer_bundle(version)
        if ui_files is None:
            sys.exit(f'no {mi.DIST}/index.html and no pilcrow-install.sh for {version} at the root: run `npm run build` first')
        source = 'pilcrow-install.sh at the root'
    ui = bundle_sha(ui_files) != base_bundle_sha(base)
    if ui:
        written.update(ui_files)
        print(f'  interface changed: bundle from {source}, {len(ui_files) - 1} chunks')
    else:
        print('  interface unchanged: no bundle in the patch')

    order = sorted(written, key=mi.lower_key)
    dirs = sorted({d for p in order if '/' in p for d in mi.parents(os.path.dirname(p))})
    mkdir = '  mkdir -p ' + ' '.join(f'"$D"/\'{d}\'' for d in dirs) + '\n' if dirs else ''
    write_all = mkdir.encode('utf-8') + b''.join(mi.heredoc(p, written[p]) for p in order)
    write_all = write_all[:-1]  # the template supplies the newline before the closing brace
    tmpl = mi.read('scripts/installer/patch.sh.tmpl')
    out = mi.fill(tmpl, {
        'VERSION': version.encode(),
        'BASE_VERSION': base_ver.encode(),
        'COUNT': str(len(order) + len(removed)).encode(),
        'UI': b'1' if ui else b'0',
        'NPM': b'1' if npm else b'0',
        'REMOVED': '\n'.join(removed).encode('utf-8'),
        'WRITTEN': '\n'.join(order).encode('utf-8'),
        'WRITE_ALL': write_all,
        'SUMS': '\n'.join(f'{mi.sha256(written[p])}  {p}' for p in order).encode('utf-8'),
    })
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'pilcrow-patch.sh')
    with open(path, 'wb') as f:
        f.write(out)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    src = [p for p in order if not p.startswith('web/ui-bundle/')]
    print(f'pilcrow-patch.sh: {len(out):,} bytes')
    print(f'  {len(src)} source files written, {len(removed)} removed, npm install: {"yes" if npm else "no"}')
    for p in src:
        print(f'    + {p}')
    for p in removed:
        print(f'    - {p}')
    if args.verify:
        verify(path, written, removed, ui)


def verify(path, written, removed, ui):
    with open(path, 'rb') as f:
        sh = f.read()
    pat = re.compile(rb"  # ---- (?P<path>[^\n]+?) \((?P<size>\d+) bytes\)\n"
                     rb"  cat > \"\$D\"/'(?P=path)' <<'(?P<eof>PILCROW_EOF_[0-9a-f]+)'\n"
                     rb"(?P<body>.*?)(?P=eof)\n", re.S)
    got = {}
    for m in pat.finditer(sh):
        p = m.group('path').decode('utf-8')
        if len(m.group('body')) != int(m.group('size')):
            sys.exit(f'verify: {p}: size mismatch')
        got[p] = m.group('body')
    if got != written:
        bad = sorted(p for p in set(got) | set(written) if got.get(p) != written.get(p))
        sys.exit('verify: the patch differs from the tree: ' + ', '.join(bad[:10]))
    sums_text = re.search(rb"PILCROW_SUMS_EOF'\n(.*?)PILCROW_SUMS_EOF", sh, re.S).group(1).decode().strip()
    sums = dict(l.split(maxsplit=1)[::-1] for l in sums_text.split('\n'))
    if set(sums) != set(got) or any(sums[p] != mi.sha256(got[p]) for p in got):
        sys.exit('verify: the sha256 list does not match the files')
    rm = re.search(rb"PILCROW_REMOVED_EOF'\n(.*?)PILCROW_REMOVED_EOF", sh, re.S).group(1).decode().split('\n')
    if [x for x in rm if x] != removed:
        sys.exit('verify: the removed list does not match')
    if ui:
        import base64, hashlib, io, tarfile
        chunks = sorted(p for p in got if p.startswith('web/ui-bundle/ui.tgz.b64.'))
        tgz = base64.b64decode(b''.join(got[c] for c in chunks))
        if hashlib.sha256(tgz).hexdigest() != got['web/ui-bundle/ui.tgz.sha256'].decode().split()[0]:
            sys.exit('verify: UI bundle checksum mismatch')
        with tarfile.open(fileobj=io.BytesIO(tgz), mode='r:gz') as t:
            if 'dist/index.html' not in t.getnames():
                sys.exit('verify: UI bundle has no dist/index.html')
    # bash must parse the whole thing
    r = subprocess.run(['bash', '-n', path], capture_output=True)
    if r.returncode != 0:
        sys.exit('verify: bash -n failed: ' + r.stderr.decode('utf-8', 'replace'))
    print(f'verify: OK ({len(got)} files in the patch match the tree, {len(removed)} removals, bash parses it)')


if __name__ == '__main__':
    main()
