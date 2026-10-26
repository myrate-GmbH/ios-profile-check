"""Walk an iOS Simulator, in German, through installing each profile in profiles/ from Safari, and
save a screenshot and the accessibility tree at every screen into out/<profile>/."""
import functools, http.server, json, os, re, subprocess, sys, threading, time

OUT = 'out'
PORT = 8765
STEPS = [  # (labels to tap, any of them; where the step is)
    (['Zulassen', 'Allow'], 'safari prompt'),
    (['Schließen', 'Close'], 'downloaded alert'),
    ('SETTINGS', 'settings'),
    (['Profil geladen', 'Profile Downloaded'], 'settings top'),
    (['Installieren', 'Install'], 'install sheet'),
    (['Installieren', 'Install'], 'warning'),
    (['Installieren', 'Install'], 'confirm sheet'),
    (['Fertig', 'Done'], 'installed'),
    (None, 'end'),
]


def run(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        print('FAILED', cmd, r.stdout[-800:], r.stderr[-800:])
    return r.stdout


def devices():
    """The newest iPhone on the newest iOS 18 runtime and on the newest runtime past it."""
    d = json.loads(run('xcrun', 'simctl', 'list', 'devices', 'available', '-j'))['devices']
    picked = {}
    for runtime in sorted((r for r in d if 'iOS' in r), key=lambda r: [int(x) for x in re.findall(r'\d+', r)]):
        major = int(re.findall(r'iOS-(\d+)', runtime)[0])
        phones = [x for x in d[runtime] if x['name'].startswith('iPhone')]
        if phones and major >= 18:
            picked['18' if major == 18 else 'newer'] = (phones[-1]['udid'], runtime, phones[-1]['name'])
    if not picked:
        raise SystemExit('no iPhone simulator')
    return list(picked.values())


class Serve(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, '.mobileconfig': 'application/x-apple-aspen-config'}
    def log_message(self, *a): pass


def serve():
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), functools.partial(Serve, directory='profiles')).serve_forever()


def fresh(udid):
    run('xcrun', 'simctl', 'shutdown', udid, check=False)
    run('xcrun', 'simctl', 'erase', udid)
    run('xcrun', 'simctl', 'boot', udid)
    run('xcrun', 'simctl', 'bootstatus', udid, '-b')
    for k, v in [('AppleLanguages', ['-array', 'de-DE']), ('AppleLocale', ['-string', 'de_DE'])]:
        run('xcrun', 'simctl', 'spawn', udid, 'defaults', 'write', 'Apple Global Domain', k, *v)
    run('xcrun', 'simctl', 'shutdown', udid)
    run('xcrun', 'simctl', 'boot', udid)
    run('xcrun', 'simctl', 'bootstatus', udid, '-b')
    run('idb', 'connect', udid, check=False)
    time.sleep(8)


def tree(udid):
    try:
        return json.loads(run('idb', 'ui', 'describe-all', '--udid', udid, '--json'))
    except ValueError:
        return []


def walk(udid, name, tag):
    d = os.path.join(OUT, tag, name)
    os.makedirs(d, exist_ok=True)
    run('xcrun', 'simctl', 'openurl', udid, f'http://127.0.0.1:{PORT}/{name}.mobileconfig')
    time.sleep(6)
    for i, (labels, where) in enumerate(STEPS):
        if labels == 'SETTINGS':
            run('xcrun', 'simctl', 'launch', udid, 'com.apple.Preferences')
            time.sleep(4)
            continue
        base = os.path.join(d, f'{i:02d}-{where.replace(" ", "-")}')
        run('xcrun', 'simctl', 'io', udid, 'screenshot', base + '.png')
        els = tree(udid)
        json.dump(els, open(base + '.json', 'w'), ensure_ascii=False, indent=1)
        print(name, where, json.dumps([e['AXLabel'] for e in els if e.get('AXLabel')], ensure_ascii=False)[:600])
        if not labels:
            break
        hit = [e for e in els if (e.get('AXLabel') or '').strip() in labels and e.get('frame')]
        if not hit:
            print(name, where, 'NOT FOUND', labels)
            continue
        f = hit[-1]['frame']
        run('idb', 'ui', 'tap', '--udid', udid, str(int(f['x'] + f['width'] / 2)), str(int(f['y'] + f['height'] / 2)))
        time.sleep(4)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    picked = devices()
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(1)
    names = sorted(p.rsplit('.', 1)[0] for p in os.listdir('profiles'))
    for udid, runtime, model in picked:
        tag = runtime.split('.')[-1]
        print('device', model, runtime, udid)
        for name in names:
            fresh(udid)
            walk(udid, name, tag)
        run('xcrun', 'simctl', 'shutdown', udid, check=False)
