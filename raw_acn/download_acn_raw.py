"""Download unfiltered ACN session records without cleaning or date conversion.

Python standard library only. Raw HTTP pages are retained for provenance.
Run with --token-file /path/to/.acn_token --output /path/to/raw_acn
(or set the ACN_API_TOKEN environment variable instead of --token-file)
"""
import argparse
import os
import base64
import csv
import json
import time
import socket
import signal

def deadline(signum, frame):
    raise TimeoutError("Request deadline exceeded")
signal.signal(signal.SIGALRM, deadline)
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

BASE = 'https://ev.caltech.edu/api/v1/'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, help='file holding the ACN API token (default: ACN_API_TOKEN env var)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sites', nargs='+', default=['caltech', 'jpl', 'office001'],
                        choices=['caltech', 'jpl', 'office001'])
    args = parser.parse_args()
    token = (args.token_file.read_text() if args.token_file else os.environ.get('ACN_API_TOKEN', '')).strip()
    if not token:
        raise SystemExit('No token: pass --token-file or set ACN_API_TOKEN')
    authorization = 'Basic ' + base64.b64encode((token + ':').encode()).decode()
    opener = build_opener(NoRedirect())
    args.output.mkdir(parents=True, exist_ok=True)
    all_rows, manifest = [], {}
    for site in args.sites:
        pages = args.output / 'pages' / site
        pages.mkdir(parents=True, exist_ok=True)
        rows, visited, totals = [], set(), []
        url = BASE + 'sessions/' + site + '?max_results=100'
        page = 0
        while url:
            if url in visited:
                raise RuntimeError('Pagination loop detected')
            visited.add(url)
            parts = urlsplit(url)
            if (parts.scheme, parts.netloc) != ('https', 'ev.caltech.edu'):
                raise RuntimeError('Unexpected pagination destination')
            page += 1
            cache = pages / ('%05d.json' % page)
            if cache.exists():
                raw = cache.read_bytes()
            else:
                for attempt in range(5):
                    try:
                        signal.alarm(20)
                        with opener.open(Request(url, headers={'Authorization': authorization}), timeout=20) as response:
                            raw = response.read()
                        signal.alarm(0)
                        break
                    except HTTPError as exc:
                        signal.alarm(0)
                        if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                            raise RuntimeError('API HTTP error %s' % exc.code) from None
                        time.sleep(min(2 ** attempt, 16))
                    except (URLError, TimeoutError, socket.timeout):
                        signal.alarm(0)
                        print("Retrying page", page, flush=True)
                        if attempt == 4:
                            raise RuntimeError('Network request failed; rerun to resume') from None
                        time.sleep(2 ** attempt)
                payload = json.loads(raw)
                if not isinstance(payload.get('_items'), list):
                    raise RuntimeError('Unexpected API response')
                tmp = cache.with_suffix('.tmp')
                tmp.write_bytes(raw)
                tmp.replace(cache)
                time.sleep(0.1)
            payload = json.loads(raw)
            rows.extend(payload['_items'])
            totals.append(payload.get('_meta', {}).get('total'))
            href = payload.get('_links', {}).get('next', {}).get('href')
            url = urljoin(BASE, href) if href else None
            if page == 1 or page % 20 == 0 or not url:
                print('%s: %s records, page %s, API total %s' % (site, len(rows), page, totals[-1]), flush=True)
        (args.output / (site + '_sessions.json')).write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
        ids = [r.get('_id') for r in rows]
        manifest[site] = {'records': len(rows), 'pages': page, 'api_totals_seen': sorted({t for t in totals if t is not None}),
                          'duplicate_ids': len(ids) - len(set(ids)), 'endpoint': BASE + 'sessions/' + site}
        all_rows.extend(rows)
    (args.output / 'all_sessions.json').write_text(json.dumps(all_rows, ensure_ascii=False), encoding='utf-8')
    fields = list(dict.fromkeys(k for row in all_rows for k in row))
    with (args.output / 'all_sessions.csv').open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Saved %s records. JSON preserves nulls and nested values; CSV renders null/missing as empty cells.' % len(all_rows))
    print('No records filtered, deduplicated, imputed or aggregated. Compare manifest record counts to API totals before claiming completeness.')

if __name__ == '__main__':
    main()
