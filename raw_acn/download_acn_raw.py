"""Download unfiltered ACN session records without cleaning or date conversion.

Python standard library only. Raw HTTP pages are retained for provenance.
Run with --token-file /path/to/.acn_token --output /path/to/raw_acn
(or set the ACN_API_TOKEN environment variable instead of --token-file)

Two download modes:
  windows (default)  one query per calendar month, filtered on connectionTime and sorted by it,
                     the same query form acnportal's DataClient.get_sessions_by_time uses. No
                     request needs a deep page offset, which is where the unfiltered listing
                     stalled (Caltech page 233). Adjacent windows share their boundary second,
                     so a session exactly on a boundary is fetched twice and kept once (by _id).
                     Raw pages are cached in pages/<site>/windows/<YYYY-MM>/.
  pages              the original unfiltered listing, cached in pages/<site>/.
Either mode resumes from its cache when rerun. A site is complete when its record count equals
the API total, which is read fresh at the end of every run.
"""
import argparse
import base64
import csv
import json
import os
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = 'https://ev.caltech.edu/api/v1/'
FIRST_MONTH = (2018, 1)  # ACN-Data begins in 2018; earlier windows would simply be empty
RFC1123 = '%a, %d %b %Y %H:%M:%S GMT'
RETRY_HTTP = (429, 500, 502, 503, 504)
SAFE_URL_CHARS = "!#$%&'()*+,/:;=?@[]~"  # what requests leaves unescaped when it requotes a URL


def deadline(signum, frame):
    raise TimeoutError('request deadline exceeded')


if hasattr(signal, 'SIGALRM'):
    signal.signal(signal.SIGALRM, deadline)


def set_alarm(seconds):
    if hasattr(signal, 'SIGALRM'):
        signal.alarm(seconds)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, base, token, timeout, retries):
        self.base = base
        self.netloc = urlsplit(base)[:2]
        self.authorization = 'Basic ' + base64.b64encode((token + ':').encode()).decode()
        self.opener = build_opener(NoRedirect())
        self.timeout = timeout
        self.retries = retries

    def get(self, url, label):
        """GET url and return the raw body; retry network errors and 429/5xx with backoff."""
        url = quote(url, safe=SAFE_URL_CHARS)  # next links can carry raw spaces and quotes
        if urlsplit(url)[:2] != self.netloc:
            raise RuntimeError('Unexpected pagination destination: %s' % url)
        for attempt in range(1, self.retries + 1):
            try:
                set_alarm(self.timeout + 5)
                started = time.monotonic()
                with self.opener.open(Request(url, headers={'Authorization': self.authorization}),
                                      timeout=self.timeout) as response:
                    raw = response.read()
                set_alarm(0)
                return raw, time.monotonic() - started
            except HTTPError as exc:
                set_alarm(0)
                if exc.code in (401, 403):
                    raise SystemExit('API HTTP error %s: the token was rejected. Check .acn_token.' % exc.code)
                if exc.code not in RETRY_HTTP or attempt == self.retries:
                    raise SystemExit('API HTTP error %s for %s' % (exc.code, label))
                reason = 'HTTP %s' % exc.code
            except (URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                set_alarm(0)
                reason = '%s: %s' % (type(exc).__name__, getattr(exc, 'reason', exc))
                if attempt == self.retries:
                    raise SystemExit('Network request failed for %s after %s attempts (%s). '
                                     'Rerun to resume from the cache.' % (label, attempt, reason))
            wait = min(2 ** attempt, 60)
            print('  retry %s/%s for %s in %ss (%s)' % (attempt, self.retries, label, wait, reason), flush=True)
            time.sleep(wait)


def fetch_listing(fetcher, url, cache_dir, label):
    """Follow _links.next from url, caching each raw page. Returns (rows, pages, totals)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows, totals, visited, page = [], [], set(), 0
    while url:
        if url in visited:
            raise RuntimeError('Pagination loop detected at %s' % url)
        visited.add(url)
        page += 1
        cache = cache_dir / ('%05d.json' % page)
        if cache.exists():
            raw = cache.read_bytes()
        else:
            raw, seconds = fetcher.get(url, '%s page %s' % (label, page))
            payload = json.loads(raw)
            if not isinstance(payload.get('_items'), list):
                raise RuntimeError('Unexpected API response for %s page %s' % (label, page))
            tmp = cache.with_suffix('.tmp')
            tmp.write_bytes(raw)
            tmp.replace(cache)
            if seconds > 10:
                print('  %s page %s took %.0fs' % (label, page, seconds), flush=True)
            time.sleep(0.1)
        payload = json.loads(raw)
        rows.extend(payload['_items'])
        totals.append(payload.get('_meta', {}).get('total'))
        href = payload.get('_links', {}).get('next', {}).get('href')
        url = urljoin(fetcher.base, href) if href else None
    return rows, page, totals


def month_starts(first, now):
    year, month = first
    while (year, month) <= (now.year, now.month):
        yield datetime(year, month, 1, tzinfo=timezone.utc)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def next_month(start):
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def window_url(base, site, start, end):
    cond = 'connectionTime >= "%s" and connectionTime <= "%s"' % (start.strftime(RFC1123), end.strftime(RFC1123))
    return base + 'sessions/' + site + '?' + urlencode({'where': cond, 'sort': 'connectionTime', 'max_results': 100})


def api_total(fetcher, site):
    raw, _ = fetcher.get(fetcher.base + 'sessions/' + site + '?max_results=1', '%s total' % site)
    return json.loads(raw).get('_meta', {}).get('total')


def download_site(fetcher, site, out, mode):
    if mode == 'pages':
        rows, pages, totals = fetch_listing(fetcher, fetcher.base + 'sessions/' + site + '?max_results=100',
                                            out / 'pages' / site, site)
        print('%s: %s records in %s pages' % (site, len(rows), pages), flush=True)
        return rows, {'pages': pages, 'api_totals_seen': sorted({t for t in totals if t is not None})}
    rows, seen, pages, months = [], set(), 0, 0
    now = datetime.now(timezone.utc)
    for start in month_starts(FIRST_MONTH, now):
        label = '%s %s' % (site, start.strftime('%Y-%m'))
        cache_dir = out / 'pages' / site / 'windows' / start.strftime('%Y-%m')
        if start.year == now.year and start.month == now.month and cache_dir.exists():
            for stale in cache_dir.glob('*.json'):  # the current month can still grow
                stale.unlink()
        window_rows, n_pages, _ = fetch_listing(fetcher, window_url(fetcher.base, site, start, next_month(start)),
                                                cache_dir, label)
        pages += n_pages
        months += 1
        new = [r for r in window_rows if r.get('_id') not in seen]
        seen.update(r.get('_id') for r in new)
        rows.extend(new)
        if window_rows:
            print('%s: %s sessions (%s total so far)' % (label, len(window_rows), len(rows)), flush=True)
    return rows, {'pages': pages, 'month_windows': months}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--token-file', type=Path, help='file holding the ACN API token (default: ACN_API_TOKEN env var)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sites', nargs='+', default=['caltech', 'jpl', 'office001'],
                        choices=['caltech', 'jpl', 'office001'])
    parser.add_argument('--mode', choices=['windows', 'pages'], default='windows')
    parser.add_argument('--timeout', type=int, default=120, help='seconds per request (default 120)')
    parser.add_argument('--retries', type=int, default=6, help='attempts per request (default 6)')
    parser.add_argument('--base-url', default=BASE, help=argparse.SUPPRESS)  # for offline testing
    args = parser.parse_args()
    token = (args.token_file.read_text() if args.token_file else os.environ.get('ACN_API_TOKEN', '')).strip()
    if not token:
        raise SystemExit('No token: pass --token-file or set ACN_API_TOKEN')
    base = args.base_url if args.base_url.endswith('/') else args.base_url + '/'
    fetcher = Fetcher(base, token, args.timeout, args.retries)
    args.output.mkdir(parents=True, exist_ok=True)

    all_rows, manifest = [], {}
    for site in args.sites:
        rows, info = download_site(fetcher, site, args.output, args.mode)
        total = api_total(fetcher, site)
        ids = [r.get('_id') for r in rows]
        complete = total is not None and len(rows) == total
        manifest[site] = {'records': len(rows), 'api_total': total, 'complete': complete, 'mode': args.mode,
                          'duplicate_ids': len(ids) - len(set(ids)), 'endpoint': base + 'sessions/' + site, **info}
        (args.output / (site + '_sessions.json')).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        print('%s: %s of %s records (%s)' % (site, len(rows), total, 'complete' if complete else 'INCOMPLETE'), flush=True)
        all_rows.extend(rows)

    (args.output / 'all_sessions.json').write_text(json.dumps(all_rows, ensure_ascii=False), encoding='utf-8')
    fields = list(dict.fromkeys(k for row in all_rows for k in row))
    with (args.output / 'all_sessions.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Saved %s records to %s. JSON preserves nulls and nested values; CSV renders null/missing as empty cells.'
          % (len(all_rows), args.output))
    print('Nothing filtered, imputed or aggregated; the only rows dropped are repeats of the same _id from '
          'overlapping month windows.')
    if not all(m['complete'] for m in manifest.values()):
        print('WARNING: record count differs from the API total for: %s (see manifest.json)'
              % ', '.join(s for s, m in manifest.items() if not m['complete']))


if __name__ == '__main__':
    main()
