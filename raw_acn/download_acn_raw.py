"""Download unfiltered ACN session records without cleaning or date conversion.

Python standard library only. Raw HTTP pages are retained for provenance.
Run with --token-file /path/to/.acn_token --output /path/to/raw_acn
(or set the ACN_API_TOKEN environment variable instead of --token-file)

Two download modes:
  windows (default)  one query per calendar month, filtered on connectionTime, the query form
                     acnportal's DataClient.get_sessions_by_time uses, sorted by connectionTime and
                     then _id so that sessions sharing a connection second page deterministically.
                     Each request stays shallow, a failing month does not stop the other months,
                     and each month is checked against its own API total (refetched once if short).
                     Adjacent windows share their boundary second; repeats are kept once by _id.
                     Raw pages are cached in pages/<site>/windows/<YYYY-MM>/.
  pages              the original unfiltered listing, cached in pages/<site>/.
Either mode resumes from its cache when rerun.

Outputs: <site>_sessions.json only when the site is complete (unique _id count equals the API
total, read fresh at the end of the run); otherwise <site>_sessions_INCOMPLETE.json. manifest.json
keeps every site seen so far, and all_sessions.json/.csv combine the complete per-site files.
"""
import argparse
import base64
import csv
import http.client
import json
import os
import shutil
import signal
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = 'https://ev.caltech.edu/api/v1/'
SITES = ['caltech', 'jpl', 'office001']
FIRST_MONTH = (2018, 1)  # ACN-Data begins in 2018; earlier windows would simply be empty
RFC1123 = '%a, %d %b %Y %H:%M:%S GMT'
RETRY_HTTP = (429, 500, 502, 503, 504)
SAFE_URL_CHARS = "!#$%&'()*+,/:;=?@[]~"  # what requests leaves unescaped when it requotes a URL
CERT_HINT = ('SSL certificate verification failed; rerunning will not help. conda: '
             '`conda install -c conda-forge ca-certificates certifi` or `export SSL_CERT_FILE=$(python -m certifi)`; '
             'python.org Python: run "Install Certificates.command" in its Applications folder.')


class FatalError(Exception):
    """A failure that retrying or rerunning cannot fix (rejected token, bad certificate, bad query)."""


class RequestFailed(Exception):
    """A request that still failed after every retry; rerunning may succeed."""


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
        """GET url and return (payload, raw body, seconds); retry transient failures with backoff."""
        url = quote(url, safe=SAFE_URL_CHARS)  # next links can carry raw spaces and quotes
        if urlsplit(url)[:2] != self.netloc:
            raise FatalError('Unexpected pagination destination: %s' % url)
        for attempt in range(1, self.retries + 1):
            try:
                set_alarm(self.timeout + 5)
                started = time.monotonic()
                with self.opener.open(Request(url, headers={'Authorization': self.authorization}),
                                      timeout=self.timeout) as response:
                    raw = response.read()
                payload = json.loads(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get('_items'), list):
                    raise ValueError('response is not an ACN listing (no _items)')
                return payload, raw, time.monotonic() - started
            except HTTPError as exc:
                detail = exc.read()[:300].decode('utf-8', 'replace').strip()
                if exc.code in (401, 403):
                    raise FatalError('API HTTP error %s: the token was rejected. Check .acn_token. %s' % (exc.code, detail))
                if exc.code not in RETRY_HTTP:
                    raise FatalError('API HTTP error %s for %s: %s' % (exc.code, label, detail))
                reason = 'HTTP %s %s' % (exc.code, detail[:80])
            except (OSError, http.client.HTTPException, ValueError) as exc:
                cause = getattr(exc, 'reason', exc)
                if isinstance(cause, ssl.SSLCertVerificationError):
                    raise FatalError(CERT_HINT + ' (%s)' % cause)
                reason = '%s: %s' % (type(cause).__name__, cause)
            finally:
                set_alarm(0)
            if attempt == self.retries:
                raise RequestFailed('%s failed after %s attempts (%s)' % (label, attempt, reason))
            wait = min(2 ** attempt, 60)
            print('  retry %s/%s for %s in %ss (%s)' % (attempt, self.retries, label, wait, reason), flush=True)
            time.sleep(wait)


def fetch_listing(fetcher, url, cache_dir, label):
    """Follow _links.next from url, caching each raw page. Returns (rows, pages, totals)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows, totals, visited, page = [], [], set(), 0
    while url:
        if url in visited:
            raise FatalError('Pagination loop detected at %s' % url)
        visited.add(url)
        page += 1
        cache = cache_dir / ('%05d.json' % page)
        if cache.exists():
            payload = json.loads(cache.read_bytes())
        else:
            payload, raw, seconds = fetcher.get(url, '%s page %s' % (label, page))
            tmp = cache.with_suffix('.tmp')
            tmp.write_bytes(raw)
            tmp.replace(cache)
            if seconds > 10:
                print('  %s page %s took %.0fs' % (label, page, seconds), flush=True)
            time.sleep(0.1)
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


def window_url(base, site, start, end, page_size):
    cond = 'connectionTime >= "%s" and connectionTime <= "%s"' % (start.strftime(RFC1123), end.strftime(RFC1123))
    return base + 'sessions/' + site + '?' + urlencode(
        {'where': cond, 'sort': 'connectionTime,_id', 'max_results': page_size})


def fetch_window(fetcher, site, start, out, page_size):
    """One month: fetch, check unique _ids against the window's own total, refetch once if short."""
    label = '%s %s' % (site, start.strftime('%Y-%m'))
    cache_dir = out / 'pages' / site / 'windows' / start.strftime('%Y-%m')
    url = window_url(fetcher.base, site, start, next_month(start), page_size)
    for attempt in (1, 2):
        rows, pages, totals = fetch_listing(fetcher, url, cache_dir, label)
        unique = len({r.get('_id') for r in rows})
        expected = totals[0] if totals else None
        if expected is None or unique == expected:
            return rows, pages, None
        if attempt == 1:
            print('  %s: %s unique sessions but the API reports %s; refetching this month' % (label, unique, expected),
                  flush=True)
            shutil.rmtree(cache_dir)
    return rows, pages, '%s unique of %s' % (unique, expected)


def api_total(fetcher, site):
    payload, _, _ = fetcher.get(fetcher.base + 'sessions/' + site + '?max_results=1', '%s total' % site)
    return payload.get('_meta', {}).get('total')


def download_site(fetcher, site, out, mode, page_size):
    """Returns (rows deduplicated by _id, manifest info)."""
    if mode == 'pages':
        raw_rows, pages, totals = fetch_listing(
            fetcher, fetcher.base + 'sessions/' + site + '?max_results=%s' % page_size, out / 'pages' / site, site)
        info = {'pages': pages, 'api_totals_seen': sorted({t for t in totals if t is not None})}
        problems = {}
    else:
        raw_rows, pages, problems = [], 0, {}
        now = datetime.now(timezone.utc)
        for start in month_starts(FIRST_MONTH, now):
            month = start.strftime('%Y-%m')
            if (start.year, start.month) == (now.year, now.month):
                shutil.rmtree(out / 'pages' / site / 'windows' / month, ignore_errors=True)  # may still grow
            try:
                rows, n_pages, short = fetch_window(fetcher, site, start, out, page_size)
            except RequestFailed as exc:
                print('  %s; continuing with the next month' % exc, flush=True)
                problems[month] = str(exc)
                continue
            if short:
                problems[month] = short
            raw_rows.extend(rows)
            pages += n_pages
            if rows:
                print('%s %s: %s sessions' % (site, month, len(rows)), flush=True)
        info = {'pages': pages, 'problem_months': problems}
    rows, seen = [], set()
    for r in raw_rows:
        if r.get('_id') not in seen:
            seen.add(r.get('_id'))
            rows.append(r)
    info['repeated_ids_dropped'] = len(raw_rows) - len(rows)
    return rows, info


def write_combined(out):
    rows = []
    for site in SITES:
        path = out / (site + '_sessions.json')
        if path.exists():
            rows.extend(json.loads(path.read_text(encoding='utf-8')))
    (out / 'all_sessions.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (out / 'all_sessions.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--token-file', type=Path, help='file holding the ACN API token (default: ACN_API_TOKEN env var)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sites', nargs='+', default=SITES, choices=SITES)
    parser.add_argument('--mode', choices=['windows', 'pages'], default='windows')
    parser.add_argument('--timeout', type=int, default=120, help='seconds per request (default 120)')
    parser.add_argument('--retries', type=int, default=6, help='attempts per request (default 6)')
    parser.add_argument('--page-size', type=int, default=100, help='records per request (default 100)')
    parser.add_argument('--base-url', default=BASE, help=argparse.SUPPRESS)  # for offline testing
    args = parser.parse_args()
    token = (args.token_file.read_text() if args.token_file else os.environ.get('ACN_API_TOKEN', '')).strip()
    if not token:
        raise SystemExit('No token: pass --token-file or set ACN_API_TOKEN')
    base = args.base_url if args.base_url.endswith('/') else args.base_url + '/'
    fetcher = Fetcher(base, token, args.timeout, args.retries)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}

    try:
        for site in args.sites:
            try:
                rows, info = download_site(fetcher, site, out, args.mode, args.page_size)
            except RequestFailed as exc:  # pages mode: the listing cannot skip a failed page
                print('  %s; rerun to resume from the cache, or use --mode windows' % exc, flush=True)
                manifest[site] = {'complete': False, 'mode': args.mode, 'error': str(exc)}
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
                continue
            try:
                total = api_total(fetcher, site)
            except RequestFailed as exc:
                print('  %s' % exc, flush=True)
                total = None
            complete = total is not None and len(rows) == total and not info.get('problem_months')
            name = site + ('_sessions.json' if complete else '_sessions_INCOMPLETE.json')
            (out / name).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
            if complete:
                (out / (site + '_sessions_INCOMPLETE.json')).unlink(missing_ok=True)
            manifest[site] = {'records': len(rows), 'api_total': total, 'complete': complete, 'mode': args.mode,
                              'file': name, 'endpoint': base + 'sessions/' + site,
                              'downloaded_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), **info}
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            print('%s: %s unique records of %s -> %s' % (site, len(rows), total, name), flush=True)
    except FatalError as exc:
        raise SystemExit(str(exc))

    n = write_combined(out)
    print('all_sessions.json/.csv: %s records from the complete per-site files.' % n)
    print('Nothing filtered, imputed or aggregated; only repeats of the same _id were dropped.')
    bad = [s for s in SITES if s in manifest and not manifest[s]['complete']]
    if bad:
        print('INCOMPLETE: %s. See manifest.json (problem_months); rerun the same command to retry from the cache.'
              % ', '.join(bad))


if __name__ == '__main__':
    main()
