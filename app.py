import argparse
import json
import os
import time
from urllib.parse import urlparse

import cloudscraper
import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

# Bypass OpenSea Cloudflare when API key alone is not accepted
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'firefox',
        'platform': 'windows',
        'mobile': False
    }
)

OPENSEA_API_BASE = 'https://api.opensea.io'
RETRYABLE_STATUS_CODES = {429, 502, 503, 504, 599}
DEFAULT_MAX_RETRIES = 5
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
DEFAULT_IMAGE_DELAY = 0.35
DEFAULT_PAGE_DELAY = 1.5

parser = argparse.ArgumentParser(description='Mass download the metadata & images for a collection of NFTs')
parser.add_argument('collection_name', action='store', type=str, help='collection name (OpenSea slug)')
parser.add_argument(
    '--page-size',
    type=int,
    default=DEFAULT_PAGE_SIZE,
    help=f'NFTs per OpenSea API page (1-{MAX_PAGE_SIZE}, default: {DEFAULT_PAGE_SIZE})',
)
parser.add_argument(
    '--delay',
    type=float,
    default=DEFAULT_IMAGE_DELAY,
    help=f'Seconds to wait after each image download (default: {DEFAULT_IMAGE_DELAY})',
)
parser.add_argument(
    '--page-delay',
    type=float,
    default=DEFAULT_PAGE_DELAY,
    help=f'Seconds to wait between OpenSea list pages (default: {DEFAULT_PAGE_DELAY})',
)
parser.add_argument(
    '--max-retries',
    type=int,
    default=DEFAULT_MAX_RETRIES,
    help=f'Max retry attempts per HTTP request (default: {DEFAULT_MAX_RETRIES})',
)
args = parser.parse_args()

if not 1 <= args.page_size <= MAX_PAGE_SIZE:
    parser.error(f'--page-size must be between 1 and {MAX_PAGE_SIZE}')

CollectionName = args.collection_name.lower()

API_KEY = '2dc6ee15cbe543a9bf57cc27769c79eb'

headers = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 12_2_1) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36'
    ),
    'X-API-KEY': API_KEY,
}

stats = {
    'DownloadedData': 0,
    'AlreadyDownloadedData': 0,
    'DownloadedImages': 0,
    'AlreadyDownloadedImages': 0,
    'FailedImages': 0,
    'PagesProcessed': 0,
}

ipfs_gateways = [
    'cf-ipfs.com',
    'gateway.ipfs.io',
    'cloudflare-ipfs.com',
    '10.via0.com',
    'gateway.pinata.cloud',
    'ipfs.cf-ipfs.com',
    'ipfs.io',
    'ipfs.sloppyta.co',
    'ipfs.best-practice.se',
    'snap1.d.tube',
    'ipfs.greyh.at',
    'ipfs.drink.cafe',
    'ipfs.2read.net',
    'robotizing.net',
    'dweb.link',
    'ninetailed.ninja',
]

IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg')

CONTENT_TYPE_TO_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/svg+xml': '.svg',
}


def parse_retry_after(response):
    retry_after = response.headers.get('Retry-After')
    if retry_after is None:
        return None
    try:
        return int(retry_after)
    except ValueError:
        return None


def retry_wait(attempt, response=None):
    if response is not None:
        header_wait = parse_retry_after(response)
        if header_wait is not None:
            return min(header_wait, 60)
    return min(2 ** attempt, 60)


def request_with_retry(method, url, *, use_scraper=False, max_retries=None, **kwargs):
    max_retries = max_retries if max_retries is not None else args.max_retries
    kwargs.setdefault('headers', headers)
    kwargs.setdefault('timeout', 30)

    last_exception = None
    last_response = None
    client = scraper if use_scraper else requests

    for attempt in range(max_retries):
        try:
            response = client.request(method, url, **kwargs)
            last_response = response

            if response.status_code in RETRYABLE_STATUS_CODES:
                wait = retry_wait(attempt, response)
                print(
                    f'  [!] HTTP {response.status_code}, retrying in {wait}s '
                    f'(attempt {attempt + 1}/{max_retries})'
                )
                time.sleep(wait)
                continue

            return response

        except (ConnectionError, Timeout) as exc:
            last_exception = exc
            wait = retry_wait(attempt)
            print(
                f'  [!] Connection error, retrying in {wait}s '
                f'(attempt {attempt + 1}/{max_retries}): {exc}'
            )
            time.sleep(wait)

    if last_exception is not None:
        raise last_exception
    return last_response


def opensea_get(path, params=None):
    url = f'{OPENSEA_API_BASE}{path}'
    response = request_with_retry('GET', url, params=params, use_scraper=False)
    if response is not None and response.status_code == 403:
        response = request_with_retry('GET', url, params=params, use_scraper=True)
    return response


def fetch_collection_page(next_cursor=None):
    params = {'limit': args.page_size}
    if next_cursor:
        params['next'] = next_cursor
    return opensea_get(f'/api/v2/collection/{CollectionName}/nfts', params=params)


def ipfs_resolve(image_url):
    cid = image_url.removeprefix('https://ipfs.io/ipfs/')
    request = None
    for gateway in ipfs_gateways:
        try:
            request = request_with_retry(
                'GET',
                f'https://{gateway}/ipfs/{cid}',
            )
            if request.status_code == 200:
                break
        except RequestException:
            continue
    return request


def resolve_image_url(asset):
    for key in ('display_image_url', 'image_url'):
        url = asset.get(key)
        if url:
            return url
    return ''


def extension_from_url(url):
    path = urlparse(url).path.lower()
    for ext in IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return '.jpg' if ext == '.jpeg' else ext
    return None


def extension_from_content_type(content_type):
    if not content_type:
        return None
    mime = content_type.split(';')[0].strip().lower()
    return CONTENT_TYPE_TO_EXT.get(mime)


def extension_from_magic(body):
    if not body:
        return None
    if body[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if body[:3] == b'\xff\xd8\xff':
        return '.jpg'
    if body[:6] in (b'GIF87a', b'GIF89a'):
        return '.gif'
    if len(body) >= 12 and body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return '.webp'
    head = body[:512].lstrip()
    if head.startswith(b'<svg') or head.startswith(b'<?xml'):
        return '.svg'
    return None


def guess_extension(image_url, content_type, body):
    ext = extension_from_url(image_url)
    if ext:
        return ext
    ext = extension_from_content_type(content_type)
    if ext:
        return ext
    ext = extension_from_magic(body)
    if ext:
        return ext
    return '.png'


def is_fake_png(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(512).lstrip()
        return head.startswith(b'<svg') or head.startswith(b'<?xml')
    except OSError:
        return False


def image_already_downloaded(collection_dir, formatted_number):
    for ext in IMAGE_EXTENSIONS:
        path = os.path.join(collection_dir, f'{formatted_number}{ext}')
        if not os.path.exists(path):
            continue
        if ext == '.png' and is_fake_png(path):
            continue
        return path
    return None


def fetch_image(image_url):
    if image_url.startswith('https://ipfs.io/ipfs/'):
        return ipfs_resolve(image_url)
    try:
        return request_with_retry('GET', image_url)
    except RequestException:
        return None


def process_nft(asset, count, collection_dir):
    token_id = str(asset['identifier'])
    formatted_number = '0' * (len(str(count)) - len(token_id)) + token_id

    print(f'\n#{formatted_number}:')

    json_path = os.path.join(collection_dir, 'image_data', f'{formatted_number}.json')
    if os.path.exists(json_path):
        print('  Data  -> [\u2713] (Already Downloaded)')
        stats['AlreadyDownloadedData'] += 1
    else:
        with open(json_path, 'w', encoding='utf-8') as dfile:
            json.dump(asset, dfile, indent=3)
        print('  Data  -> [\u2713] (Successfully downloaded)')
        stats['DownloadedData'] += 1

    existing_image = image_already_downloaded(collection_dir, formatted_number)
    if existing_image:
        ext = os.path.splitext(existing_image)[1]
        print(f'  Image -> [\u2713] (Already Downloaded{ext})')
        stats['AlreadyDownloadedImages'] += 1
        return

    image_url = resolve_image_url(asset)
    if not image_url:
        print('  Image -> [!] (Blank URL)')
        stats['FailedImages'] += 1
        return

    image = fetch_image(image_url)
    if args.delay > 0:
        time.sleep(args.delay)

    if image is None or image.status_code != 200:
        status = image.status_code if image is not None else 'unknown'
        print(f'  Image -> [!] (HTTP Status {status})')
        stats['FailedImages'] += 1
        return

    ext = guess_extension(image_url, image.headers.get('Content-Type'), image.content)
    out_path = os.path.join(collection_dir, f'{formatted_number}{ext}')
    with open(out_path, 'wb') as file:
        file.write(image.content)
    print(f'  Image -> [\u2713] (Successfully downloaded as {ext})')
    stats['DownloadedImages'] += 1


# --- Fetch collection metadata ---
try:
    collection = opensea_get(f'/api/v2/collections/{CollectionName}', params={'format': 'json'})
except RequestException as exc:
    print(f'Failed to reach OpenSea API: {exc}')
    raise SystemExit(1) from exc

if collection is None:
    print('Failed to reach OpenSea API after retries.')
    raise SystemExit(1)

if collection.status_code == 429:
    print('Server returned HTTP 429. Request was throttled. Please try again in about 5 minutes.')
    raise SystemExit(1)

if collection.status_code == 404:
    print(
        'NFT Collection not found.\n\n'
        f'(Hint: Check the collection slug "{CollectionName}" matches OpenSea.)'
    )
    raise SystemExit(1)

if collection.status_code != 200:
    print(f'OpenSea API returned HTTP {collection.status_code} for collection metadata.')
    raise SystemExit(1)

collectioninfo = collection.json()
count = int(collectioninfo['total_supply'])
collection_label = collectioninfo.get('name', CollectionName)

print(f'Collection: {collection_label} (reported supply: {count})')

# --- Prepare output directories ---
os.makedirs('./images', exist_ok=True)
collection_dir = f'./images/{CollectionName}'
os.makedirs(collection_dir, exist_ok=True)
os.makedirs(f'{collection_dir}/image_data', exist_ok=True)

print(
    f'\nBeginning download of "{CollectionName}" '
    f'(page size: {args.page_size}, image delay: {args.delay}s, page delay: {args.page_delay}s).\n'
)

# --- Paginate with cursor (OpenSea API v2) ---
page_index = 0
next_cursor = None
run_status = 'completed'
exit_code = 0

while True:
    page_index += 1

    try:
        response = fetch_collection_page(next_cursor)
    except RequestException as exc:
        print(f'\n[!] Failed to fetch NFT page {page_index} after retries: {exc}')
        print('Re-run the same command to resume from already downloaded files.')
        run_status = 'interrupted'
        exit_code = 1
        break

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else 'unknown'
        print(f'\n[!] OpenSea API returned HTTP {status} on page {page_index}.')
        print('Re-run the same command to resume from already downloaded files.')
        run_status = 'interrupted'
        exit_code = 1
        break

    data = response.json()
    nfts = data.get('nfts', [])
    stats['PagesProcessed'] = page_index
    print(f'\n--- Page {page_index}: {len(nfts)} NFTs ---')

    for asset in nfts:
        try:
            process_nft(asset, count, collection_dir)
        except Exception as exc:
            token_id = asset.get('identifier', '?')
            print(f'  [!] Unexpected error on token {token_id}: {exc}')
            stats['FailedImages'] += 1

    next_cursor = data.get('next')
    if not next_cursor or not nfts:
        break

    if args.page_delay > 0:
        time.sleep(args.page_delay)

print(f"""

Finished downloading collection ({run_status}).


Statistics
-=-=-=-=-=-

Collection "{CollectionName}" (reported supply: {count}).
API pages processed: {stats['PagesProcessed']}

Downloads:

    JSON Files ->
    {stats['DownloadedData']} successfully downloaded
    {stats['AlreadyDownloadedData']} already downloaded

    Images ->
    {stats['DownloadedImages']} successfully downloaded
    {stats['AlreadyDownloadedImages']} already downloaded
    {stats['FailedImages']} failed


You can find the images in the images/{CollectionName} folder.
Images are saved with the correct extension (.png, .svg, .jpg, etc.) for each token.
The JSON for each NFT can be found in the images/{CollectionName}/image_data folder.

If the run was interrupted, re-run: python app.py {CollectionName}
Press enter to exit...""")

input()
raise SystemExit(exit_code)
