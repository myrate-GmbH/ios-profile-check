"""Walk an iOS Simulator, in German, through installing each profile in profiles/ from Safari, and
save a screenshot and the accessibility tree at every screen into out/<profile>/."""
import http.server, json, os, subprocess, sys, threading, time

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


def device():
    d = json.loads(run('xcrun', 'simctl', 'list', 'devices', 'available', '-j'))['devices']
    for runtime in sorted(d, reverse=True):
        if 'iOS' in runtime:
            for dev in d[runtime]:
                if dev['name'].startswith('iPhone 16'):
                    return dev['udid'], runtime, dev['name']
    raise SystemExit('no iPhone simulator')


class Serve(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, '.mobileconfig': 'application/x-apple-aspen-config'}
    def log_message(self, *a): pass


def serve():
    os.chdir('profiles')
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Serve).serve_forever()


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
    time.sleep(8)


def tree(udid):
    try:
        return json.loads(run('idb', 'ui', 'describe-all', '--udid', udid, '--json'))
    except ValueError:
        return []


def walk(udid, name):
    d = os.path.join(OUT, name)
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
    udid, runtime, model = device()
    print('device', model, runtime, udid)
    threading.Thread(target=serve, daemon=True).start()
    for p in sorted(os.listdir('profiles')):
        name = p.rsplit('.', 1)[0]
        fresh(udid)
        walk(udid, name)
