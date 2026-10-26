"""Walk an iOS Simulator, in German, through installing each profile in profiles/ from Safari, and
save a screenshot and the text Vision reads on it at every screen into out/<runtime>/<profile>/."""
import functools, http.server, json, os, re, subprocess, threading, time

OUT = 'out'
PORT = 8765
# (labels to tap, any of them; which of several matches; where the step is)
STEPS = [
    (['Zulassen', 'Allow'], 'last', 'safari prompt'),
    (['Schließen', 'Close'], 'last', 'downloaded alert'),
    ('SETTINGS', None, 'settings'),
    (['Profil geladen', 'Profile Downloaded'], 'first', 'settings top'),
    (['Installieren', 'Install'], 'top', 'install sheet'),
    (['Installieren', 'Install'], 'top', 'warning'),
    (['Installieren', 'Install'], 'bottom', 'confirm sheet'),
    (['Fertig', 'Done'], 'top', 'installed'),
    (None, None, 'end'),
]


def run(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        print('FAILED', cmd, (r.stdout + r.stderr)[-400:])
    return r.stdout


def devices():
    """A current iPhone on the newest iOS 18 runtime and on the newest runtime past it."""
    d = json.loads(run('xcrun', 'simctl', 'list', 'devices', 'available', '-j'))['devices']
    picked = {}
    for runtime in sorted((r for r in d if 'iOS' in r), key=lambda r: [int(x) for x in re.findall(r'\d+', r)]):
        major = int(re.findall(r'iOS-(\d+)', runtime)[0])
        phones = [x for x in d[runtime] if re.match(r'iPhone 1[6-9]( Pro)?$', x['name'])] or [x for x in d[runtime] if x['name'].startswith('iPhone')]
        if phones and major >= 18:
            picked['18' if major == 18 else 'newer'] = (phones[0]['udid'], runtime, phones[0]['name'])
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
    time.sleep(10)


def scale(udid):
    try:
        return float(json.loads(run('idb', 'describe', '--udid', udid, '--json'))['screen_dimensions']['density'])
    except Exception:
        return 3.0


def ocr(png):
    """Every line of text Vision reads on the screenshot, with its centre in pixels."""
    import Quartz, Vision
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(os.path.abspath(png)), None)
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLanguages_(['de-DE', 'en-US'])
    req.setUsesLanguageCorrection_(False)
    Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None).performRequests_error_([req], None)
    lines = []
    for o in req.results() or []:
        b = o.boundingBox()
        lines.append({'text': str(o.topCandidates_(1)[0].string()), 'x': (b.origin.x + b.size.width / 2) * w, 'y': (1 - b.origin.y - b.size.height / 2) * h})
    return sorted(lines, key=lambda l: (l['y'], l['x']))


def open_profile(udid, name):
    run('xcrun', 'simctl', 'launch', udid, 'com.apple.mobilesafari', check=False)
    time.sleep(8)
    for _ in range(3):
        r = subprocess.run(['xcrun', 'simctl', 'openurl', udid, f'http://127.0.0.1:{PORT}/{name}.mobileconfig'], capture_output=True, text=True)
        if r.returncode == 0:
            break
        time.sleep(5)
    time.sleep(8)


def walk(udid, name, tag):
    d = os.path.join(OUT, tag, name)
    os.makedirs(d, exist_ok=True)
    k = scale(udid)
    open_profile(udid, name)
    for i, (labels, pick, where) in enumerate(STEPS):
        if labels == 'SETTINGS':
            run('xcrun', 'simctl', 'launch', udid, 'com.apple.Preferences')
            time.sleep(5)
            continue
        base = os.path.join(d, f'{i:02d}-{where.replace(" ", "-")}')
        run('xcrun', 'simctl', 'io', udid, 'screenshot', base + '.png')
        lines = ocr(base + '.png')
        json.dump(lines, open(base + '.json', 'w'), ensure_ascii=False, indent=1)
        print(tag, name, where, json.dumps([l['text'] for l in lines], ensure_ascii=False)[:700])
        if not labels:
            break
        hits = [l for l in lines if l['text'].strip() in labels]
        if not hits:
            print(tag, name, where, 'NOT FOUND', labels)
            continue
        hit = {'first': hits[0], 'last': hits[-1], 'top': min(hits, key=lambda l: l['y']), 'bottom': max(hits, key=lambda l: l['y'])}[pick]
        run('idb', 'ui', 'tap', '--udid', udid, str(int(hit['x'] / k)), str(int(hit['y'] / k)))
        time.sleep(5)


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    threading.Thread(target=serve, daemon=True).start()
    names = sorted(p.rsplit('.', 1)[0] for p in os.listdir('profiles'))
    for udid, runtime, model in devices():
        tag = runtime.split('.')[-1]
        print('device', model, runtime)
        for name in names:
            fresh(udid)
            walk(udid, name, tag)
        run('xcrun', 'simctl', 'shutdown', udid, check=False)
