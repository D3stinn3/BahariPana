import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

import cloudscraper
import requests
from requests.exceptions import ConnectionError, RequestException, Timeout

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
DEFAULT_BATCH_SIZE = 30
MAX_BATCH_SIZE = 50
DEFAULT_IMAGE_DELAY = 0.35
DEFAULT_PAGE_DELAY = 1.5

API_KEY = '2dc6ee15cbe543a9bf57cc27769c79eb'

headers = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 12_2_1) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36'
    ),
    'X-API-KEY': API_KEY,
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

args = None
CollectionName = None

stats = {}


def reset_stats():
    global stats
    stats = {
        'DownloadedData': 0,
        'AlreadyDownloadedData': 0,
        'DownloadedImages': 0,
        'AlreadyDownloadedImages': 0,
        'FailedImages': 0,
        'PagesProcessed': 0,
        'BatchesProcessed': 0,
        'NotFound': 0,
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description='Mass download or repair missing metadata and images for an NFT collection',
    )
    subparsers = parser.add_subparsers(dest='command')

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument('collection_name', type=str, help='OpenSea collection slug')
    shared.add_argument(
        '--delay',
        type=float,
        default=DEFAULT_IMAGE_DELAY,
        help=f'Seconds to wait after each image HTTP request (default: {DEFAULT_IMAGE_DELAY})',
    )
    shared.add_argument(
        '--max-retries',
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f'Max retry attempts per HTTP request (default: {DEFAULT_MAX_RETRIES})',
    )

    download = subparsers.add_parser(
        'download',
        parents=[shared],
        help='Download full collection (default mode)',
        description='Paginate the collection and download all metadata and images',
    )
    download.add_argument(
        '--page-size',
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f'NFTs per OpenSea API page (1-{MAX_PAGE_SIZE}, default: {DEFAULT_PAGE_SIZE})',
    )
    download.add_argument(
        '--page-delay',
        type=float,
        default=DEFAULT_PAGE_DELAY,
        help=f'Seconds to wait between OpenSea list pages (default: {DEFAULT_PAGE_DELAY})',
    )

    repair = subparsers.add_parser(
        'repair',
        parents=[shared],
        help='Fill in missing JSON and/or images only',
        description='Target gaps without re-downloading the full collection',
    )
    repair.add_argument(
        '--dry-run',
        action='store_true',
        help='List missing token IDs without downloading',
    )
    repair.add_argument(
        '--json-only',
        action='store_true',
        help='Only fetch missing metadata JSON files',
    )
    repair.add_argument(
        '--images-only',
        action='store_true',
        help='Only fetch images where JSON exists but the image file is missing',
    )
    repair.add_argument(
        '--ids',
        type=str,
        help='Comma-separated token IDs to repair (e.g. 21,22,23)',
    )
    repair.add_argument(
        '--from-file',
        type=str,
        help='Path to a file with one token ID per line',
    )
    repair.add_argument('--min-id', type=int, help='Minimum token ID for gap scan (inclusive)')
    repair.add_argument('--max-id', type=int, help='Maximum token ID for gap scan (inclusive)')
    repair.add_argument(
        '--batch-size',
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f'NFTs per OpenSea batch request (1-{MAX_BATCH_SIZE}, default: {DEFAULT_BATCH_SIZE})',
    )
    repair.add_argument(
        '--page-delay',
        type=float,
        default=DEFAULT_PAGE_DELAY,
        help=f'Seconds to wait between batch API requests (default: {DEFAULT_PAGE_DELAY})',
    )

    return parser


def parse_args():
    parser = build_parser()
    argv = sys.argv[1:]
    if argv and argv[0] not in ('download', 'repair', '-h', '--help'):
        argv = ['download', *argv]
    parsed = parser.parse_args(argv)
    if parsed.command is None:
        parser.error('the following arguments are required: collection_name')
    return parsed


def format_token_id(token_id, total_supply):
    token_id = str(token_id)
    return '0' * (len(str(total_supply)) - len(token_id)) + token_id


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


def opensea_post(path, json_body):
    url = f'{OPENSEA_API_BASE}{path}'
    response = request_with_retry('POST', url, json=json_body, use_scraper=False)
    if response is not None and response.status_code == 403:
        response = request_with_retry('POST', url, json=json_body, use_scraper=True)
    return response


def fetch_collection_page(next_cursor=None):
    params = {'limit': args.page_size}
    if next_cursor:
        params['next'] = next_cursor
    return opensea_get(f'/api/v2/collection/{CollectionName}/nfts', params=params)


def fetch_nfts_batch(chain, contract_address, token_ids):
    payload = {
        'nfts': [
            {
                'chain': chain,
                'address': contract_address,
                'token_id': str(token_id),
            }
            for token_id in token_ids
        ]
    }
    return opensea_post('/api/v2/nfts/batch', payload)


def fetch_nft_single(chain, contract_address, token_id):
    path = f'/api/v2/chain/{chain}/contract/{contract_address}/nfts/{token_id}'
    return opensea_get(path)


def extract_nft_from_response(data):
    if not isinstance(data, dict):
        return None
    if 'nft' in data:
        return data['nft']
    if 'nfts' in data and data['nfts']:
        return data['nfts'][0]
    return None


def load_collection_metadata():
    try:
        response = opensea_get(f'/api/v2/collections/{CollectionName}', params={'format': 'json'})
    except RequestException as exc:
        print(f'Failed to reach OpenSea API: {exc}')
        raise SystemExit(1) from exc

    if response is None:
        print('Failed to reach OpenSea API after retries.')
        raise SystemExit(1)

    if response.status_code == 429:
        print('Server returned HTTP 429. Request was throttled. Please try again in about 5 minutes.')
        raise SystemExit(1)

    if response.status_code == 404:
        print(
            'NFT Collection not found.\n\n'
            f'(Hint: Check the collection slug "{CollectionName}" matches OpenSea.)'
        )
        raise SystemExit(1)

    if response.status_code != 200:
        print(f'OpenSea API returned HTTP {response.status_code} for collection metadata.')
        raise SystemExit(1)

    return response.json()


def resolve_contract(collectioninfo, collection_dir):
    contracts = collectioninfo.get('contracts') or []
    if contracts:
        chain = contracts[0].get('chain') or 'ethereum'
        address = contracts[0].get('address')
        if address:
            return chain, address

    image_data_dir = os.path.join(collection_dir, 'image_data')
    if os.path.isdir(image_data_dir):
        for name in sorted(os.listdir(image_data_dir)):
            if not name.endswith('.json'):
                continue
            json_path = os.path.join(image_data_dir, name)
            try:
                with open(json_path, encoding='utf-8') as handle:
                    sample = json.load(handle)
                address = sample.get('contract')
                if address:
                    return 'ethereum', address
            except (OSError, json.JSONDecodeError):
                continue

    print('Could not determine contract address for this collection.')
    raise SystemExit(1)


def setup_collection_dir():
    os.makedirs('./images', exist_ok=True)
    collection_dir = f'./images/{CollectionName}'
    os.makedirs(collection_dir, exist_ok=True)
    os.makedirs(os.path.join(collection_dir, 'image_data'), exist_ok=True)
    return collection_dir


def parse_token_id_list():
    token_ids = []

    if args.ids:
        for part in args.ids.split(','):
            part = part.strip()
            if part:
                token_ids.append(int(part))

    if args.from_file:
        with open(args.from_file, encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith('#'):
                    token_ids.append(int(line))

    return sorted(set(token_ids))


def scan_id_range(total_supply):
    min_id = args.min_id if args.min_id is not None else 0
    max_id = args.max_id if args.max_id is not None else total_supply - 1
    if min_id < 0 or max_id >= total_supply or min_id > max_id:
        print(f'Invalid ID range {min_id}-{max_id} for supply {total_supply}.')
        raise SystemExit(1)
    return min_id, max_id


def find_missing_json(collection_dir, total_supply):
    min_id, max_id = scan_id_range(total_supply)
    image_data_dir = os.path.join(collection_dir, 'image_data')
    missing = []
    for token_id in range(min_id, max_id + 1):
        formatted = format_token_id(token_id, total_supply)
        if not os.path.exists(os.path.join(image_data_dir, f'{formatted}.json')):
            missing.append(token_id)
    return missing


def find_missing_images(collection_dir, total_supply):
    min_id, max_id = scan_id_range(total_supply)
    image_data_dir = os.path.join(collection_dir, 'image_data')
    missing = []
    for token_id in range(min_id, max_id + 1):
        formatted = format_token_id(token_id, total_supply)
        json_path = os.path.join(image_data_dir, f'{formatted}.json')
        if not os.path.exists(json_path):
            continue
        if image_already_downloaded(collection_dir, formatted) is None:
            missing.append(token_id)
    return missing


def build_repair_queue(collection_dir, total_supply):
    if args.json_only and args.images_only:
        print('Use only one of --json-only or --images-only.')
        raise SystemExit(1)

    explicit_ids = parse_token_id_list()
    if explicit_ids:
        return explicit_ids

    if args.images_only:
        return find_missing_images(collection_dir, total_supply)

    if args.json_only:
        return find_missing_json(collection_dir, total_supply)

    missing_json = set(find_missing_json(collection_dir, total_supply))
    missing_images = set(find_missing_images(collection_dir, total_supply))
    return sorted(missing_json | missing_images)


def ipfs_resolve(image_url):
    cid = image_url.removeprefix('https://ipfs.io/ipfs/')
    request = None
    for gateway in ipfs_gateways:
        try:
            request = request_with_retry('GET', f'https://{gateway}/ipfs/{cid}')
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
        with open(path, 'rb') as handle:
            head = handle.read(512).lstrip()
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


def process_nft(asset, total_supply, collection_dir, *, json_only=False, images_only=False):
    token_id = str(asset['identifier'])
    formatted_number = format_token_id(token_id, total_supply)

    print(f'\n#{formatted_number}:')

    json_path = os.path.join(collection_dir, 'image_data', f'{formatted_number}.json')

    if images_only:
        if not os.path.exists(json_path):
            print('  Data  -> [!] (JSON missing; run repair without --images-only)')
            stats['FailedImages'] += 1
            return
        print('  Data  -> [\u2713] (Using existing JSON)')
        stats['AlreadyDownloadedData'] += 1
    elif os.path.exists(json_path):
        print('  Data  -> [\u2713] (Already Downloaded)')
        stats['AlreadyDownloadedData'] += 1
    else:
        with open(json_path, 'w', encoding='utf-8') as dfile:
            json.dump(asset, dfile, indent=3)
        print('  Data  -> [\u2713] (Successfully downloaded)')
        stats['DownloadedData'] += 1

    if json_only:
        return

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


def repair_images_from_disk(token_id, total_supply, collection_dir):
    formatted = format_token_id(token_id, total_supply)
    json_path = os.path.join(collection_dir, 'image_data', f'{formatted}.json')
    try:
        with open(json_path, encoding='utf-8') as handle:
            asset = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'\n#{formatted}:')
        print(f'  [!] Could not read existing JSON: {exc}')
        stats['FailedImages'] += 1
        return
    try:
        process_nft(
            asset,
            total_supply,
            collection_dir,
            json_only=False,
            images_only=True,
        )
    except Exception as exc:
        print(f'  [!] Unexpected error on token {token_id}: {exc}')
        stats['FailedImages'] += 1


def fetch_and_process_batch(token_ids, total_supply, collection_dir, chain, contract_address):
    try:
        response = fetch_nfts_batch(chain, contract_address, token_ids)
    except RequestException as exc:
        print(f'  [!] Batch request failed: {exc}')
        return list(token_ids)

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else 'unknown'
        print(f'  [!] Batch API returned HTTP {status}')
        return list(token_ids)

    data = response.json()
    nfts = data.get('nfts', [])
    returned_ids = {str(asset.get('identifier', '')) for asset in nfts}

    for asset in nfts:
        identifier = str(asset.get('identifier', ''))
        try:
            process_nft(
                asset,
                total_supply,
                collection_dir,
                json_only=args.json_only,
                images_only=False,
            )
        except Exception as exc:
            print(f'  [!] Unexpected error on token {identifier}: {exc}')
            stats['FailedImages'] += 1

    return [token_id for token_id in token_ids if str(token_id) not in returned_ids]


def repair_single_fallback(token_id, total_supply, collection_dir, chain, contract_address):
    formatted = format_token_id(token_id, total_supply)
    print(f'\n--- Fallback single fetch: #{formatted} ---')

    try:
        response = fetch_nft_single(chain, contract_address, token_id)
    except RequestException as exc:
        print(f'  [!] Request failed: {exc}')
        stats['NotFound'] += 1
        return token_id

    if response is None or response.status_code == 404:
        print(f'  [!] Not found on OpenSea (token {token_id})')
        stats['NotFound'] += 1
        return token_id

    if response.status_code != 200:
        print(f'  [!] HTTP {response.status_code}')
        stats['NotFound'] += 1
        return token_id

    asset = extract_nft_from_response(response.json())
    if asset is None:
        print(f'  [!] Empty response for token {token_id}')
        stats['NotFound'] += 1
        return token_id

    try:
        process_nft(
            asset,
            total_supply,
            collection_dir,
            json_only=args.json_only,
            images_only=False,
        )
    except Exception as exc:
        print(f'  [!] Unexpected error on token {token_id}: {exc}')
        stats['FailedImages'] += 1
        return token_id

    return None


def run_download(collectioninfo, collection_dir, total_supply):
    collection_label = collectioninfo.get('name', CollectionName)
    print(f'Collection: {collection_label} (reported supply: {total_supply})')
    print(
        f'\nBeginning download of "{CollectionName}" '
        f'(page size: {args.page_size}, image delay: {args.delay}s, '
        f'page delay: {args.page_delay}s).\n'
    )

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
                process_nft(asset, total_supply, collection_dir)
            except Exception as exc:
                token_id = asset.get('identifier', '?')
                print(f'  [!] Unexpected error on token {token_id}: {exc}')
                stats['FailedImages'] += 1

        next_cursor = data.get('next')
        if not next_cursor or not nfts:
            break

        if args.page_delay > 0:
            time.sleep(args.page_delay)

    return run_status, exit_code


def run_repair(collectioninfo, collection_dir, total_supply):
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        print(f'--batch-size must be between 1 and {MAX_BATCH_SIZE}.')
        raise SystemExit(1)

    chain, contract_address = resolve_contract(collectioninfo, collection_dir)
    repair_queue = build_repair_queue(collection_dir, total_supply)

    print(f'Collection: {collectioninfo.get("name", CollectionName)} (reported supply: {total_supply})')
    print(f'Contract: {contract_address} ({chain})')
    print(f'Repair queue: {len(repair_queue)} token(s)')

    if not repair_queue:
        print('\nNothing to repair — all targeted files are present.')
        return 'completed', 0

    if args.dry_run:
        preview = repair_queue[:20]
        formatted_preview = [format_token_id(token_id, total_supply) for token_id in preview]
        print('\nDry run — missing token IDs (first 20):')
        for formatted in formatted_preview:
            print(f'  {formatted}')
        if len(repair_queue) > 20:
            print(f'  ... and {len(repair_queue) - 20} more')
        print(f'\nTotal: {len(repair_queue)} token(s) would be repaired.')
        print(f'Run without --dry-run: python app.py repair {CollectionName}')
        return 'completed', 0

    mode = 'json + images'
    if args.json_only:
        mode = 'json only'
    elif args.images_only:
        mode = 'images only'

    print(
        f'\nRepairing "{CollectionName}" ({mode}, batch size: {args.batch_size}, '
        f'delay: {args.delay}s, batch delay: {args.page_delay}s).\n'
    )

    not_found_all = []
    run_status = 'completed'
    exit_code = 0

    if args.images_only:
        for index, token_id in enumerate(repair_queue, start=1):
            repair_images_from_disk(token_id, total_supply, collection_dir)
            stats['BatchesProcessed'] = index
            if args.page_delay > 0 and index < len(repair_queue):
                time.sleep(args.page_delay)
    else:
        for batch_start in range(0, len(repair_queue), args.batch_size):
            batch = repair_queue[batch_start:batch_start + args.batch_size]
            batch_num = batch_start // args.batch_size + 1
            print(f'\n=== Repair batch {batch_num} ({len(batch)} token(s)) ===')

            not_found = fetch_and_process_batch(
                batch, total_supply, collection_dir, chain, contract_address,
            )
            stats['BatchesProcessed'] = batch_num

            for token_id in not_found:
                remaining = repair_single_fallback(
                    token_id, total_supply, collection_dir, chain, contract_address,
                )
                if remaining is not None:
                    not_found_all.append(remaining)

            if args.page_delay > 0 and batch_start + args.batch_size < len(repair_queue):
                time.sleep(args.page_delay)

    if not_found_all:
        log_path = os.path.join(collection_dir, 'repair_not_found.json')
        with open(log_path, 'w', encoding='utf-8') as handle:
            json.dump({'token_ids': not_found_all}, handle, indent=2)
        print(f'\n[!] {len(not_found_all)} token(s) not found on OpenSea. Log: {log_path}')
        exit_code = 1

    return run_status, exit_code


def print_summary(command, run_status, exit_code, total_supply):
    if command == 'repair':
        print(f"""

Finished repair ({run_status}).


Statistics
-=-=-=-=-=-

Collection "{CollectionName}" (reported supply: {total_supply}).
Repair batches processed: {stats['BatchesProcessed']}

    JSON Files ->
    {stats['DownloadedData']} successfully downloaded
    {stats['AlreadyDownloadedData']} already present / reused

    Images ->
    {stats['DownloadedImages']} successfully downloaded
    {stats['AlreadyDownloadedImages']} already downloaded
    {stats['FailedImages']} failed

    Not found on OpenSea -> {stats['NotFound']}


Re-run repair: python app.py repair {CollectionName}
Dry run:       python app.py repair {CollectionName} --dry-run
Press enter to exit...""")
    else:
        print(f"""

Finished downloading collection ({run_status}).


Statistics
-=-=-=-=-=-

Collection "{CollectionName}" (reported supply: {total_supply}).
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

If the run was interrupted, re-run: python app.py download {CollectionName}
Press enter to exit...""")


def main():
    global args, CollectionName

    args = parse_args()

    if args.command == 'download' and not 1 <= args.page_size <= MAX_PAGE_SIZE:
        print(f'--page-size must be between 1 and {MAX_PAGE_SIZE}.')
        raise SystemExit(1)

    CollectionName = args.collection_name.lower()
    reset_stats()

    collectioninfo = load_collection_metadata()
    total_supply = int(collectioninfo['total_supply'])
    collection_dir = setup_collection_dir()

    if args.command == 'repair':
        run_status, exit_code = run_repair(collectioninfo, collection_dir, total_supply)
    else:
        run_status, exit_code = run_download(collectioninfo, collection_dir, total_supply)

    print_summary(args.command, run_status, exit_code, total_supply)
    if not (args.command == 'repair' and args.dry_run):
        input()
    raise SystemExit(exit_code)


if __name__ == '__main__':
    main()
