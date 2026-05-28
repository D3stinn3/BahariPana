import requests
import os
import json
import math
from urllib.parse import urlparse
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
import cloudscraper
import argparse

# This creates a new Scraper instance that can get past the OpenSea Cloudflare protections
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'firefox',
        'platform': 'windows',
        'mobile': False
    }
)

# get collection name from arguments
parser = argparse.ArgumentParser(description='Mass download the metadata & images for a collection of NFTs')
parser.add_argument('collection_name', action='store', type=str, help='collection name to parse')
args = parser.parse_args()

# This is where you add the collection name to the URL
CollectionName = args.collection_name.lower()


# Random User Agent
software_names = [SoftwareName.CHROME.value]
operating_systems = [OperatingSystem.WINDOWS.value, OperatingSystem.LINUX.value]
user_agent_rotator = UserAgent(software_names=software_names, operating_systems=operating_systems, limit=100)
user_agent = user_agent_rotator.get_random_user_agent()

API_KEY = '2dc6ee15cbe543a9bf57cc27769c79eb'

# Headers for the request. Currently this is generating random user agents
# Use a custom header version here -> https://www.whatismybrowser.com/guides/the-latest-user-agent/
headers = {
    'User-Agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
    'X-API-KEY': API_KEY
}

# Get information regarding collection

collection = requests.get(f"http://api.opensea.io/api/v2/collections/{CollectionName}?format=json", headers=headers)

if collection.status_code == 429:
    print("Server returned HTTP 429. Request was throttled. Please try again in about 5 minutes.")
    exit()

if collection.status_code == 404:
    print("NFT Collection not found.\n\n(Hint: Try changing the name of the collection in the Python script, line 6.)")
    exit()

collectioninfo = json.loads(collection.content.decode())

print(collectioninfo)

# Create image folder if it doesn't exist.

if not os.path.exists('./images'):
    os.mkdir('./images')

if not os.path.exists(f'./images/{CollectionName}'):
    os.mkdir(f'./images/{CollectionName}')

if not os.path.exists(f'./images/{CollectionName}/image_data'):
    os.mkdir(f'./images/{CollectionName}/image_data')

# Get total NFT count

count = int(collectioninfo["total_supply"])
# Opensea limits to 30 assets per API request, so here we do the division and round up.

initial_count = count / 200

iter = math.ceil(count / 200)

print(f"\nBeginning download of \"{CollectionName}\" collection.\n")

# Define variables for statistics

stats = {
    "DownloadedData": 0,
    "AlreadyDownloadedData": 0,
    "DownloadedImages": 0,
    "AlreadyDownloadedImages": 0,
    "FailedImages": 0
}

# Define IPFS Gateways

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
    'ninetailed.ninja'
]


# Create IPFS download function
def ipfs_resolve(image_url):
    cid = image_url.removeprefix("https://ipfs.io/ipfs/")
    request = None
    for gateway in ipfs_gateways:
        request = requests.get(f"https://{gateway}/ipfs/{cid}", headers=headers, timeout=30)
        if request.status_code == 200:
            break
    return request


IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg')

CONTENT_TYPE_TO_EXT = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/svg+xml': '.svg',
}


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
    return requests.get(image_url, headers=headers, timeout=30)


collection_dir = f'./images/{CollectionName}'

# Iterate through every unit
for i in range(iter):
    offset = i * 200
    limit = f"limit=200&offset={offset}"

    if i > 0:
        limit += f"&next={next_param}"

    data = json.loads(scraper.get(f"https://api.opensea.io/api/v2/collection/{CollectionName}/nfts?{limit}", headers=headers).text)

    next_param = data.get('next', '')
    
    print(data)

    if "nfts" in data:
        for asset in data["nfts"]:
            id = str(asset['identifier'])

            formatted_number = "0" * (len(str(count)) - len(id)) + id

            print(f"\n#{formatted_number}:")

            # Check if data for the NFT already exists, if it does, skip saving it
            if os.path.exists(f'./images/{CollectionName}/image_data/{formatted_number}.json'):
                print(f"  Data  -> [\u2713] (Already Downloaded)")
                stats["AlreadyDownloadedData"] += 1
            else:
                # Take the JSON from the URL, and dump it to the respective file.
                dfile = open(f"./images/{CollectionName}/image_data/{formatted_number}.json", "w+")
                json.dump(asset, dfile, indent=3)
                dfile.close()
                print(f"  Data  -> [\u2713] (Successfully downloaded)")
                stats["DownloadedData"] += 1

            existing_image = image_already_downloaded(collection_dir, formatted_number)
            if existing_image:
                ext = os.path.splitext(existing_image)[1]
                print(f"  Image -> [\u2713] (Already Downloaded{ext})")
                stats["AlreadyDownloadedImages"] += 1
                continue

            image_url = resolve_image_url(asset)
            if not image_url:
                print(f"  Image -> [!] (Blank URL)")
                stats["FailedImages"] += 1
                continue

            image = fetch_image(image_url)
            if image is None or image.status_code != 200:
                status = image.status_code if image is not None else 'unknown'
                print(f"  Image -> [!] (HTTP Status {status})")
                stats["FailedImages"] += 1
                continue

            ext = guess_extension(image_url, image.headers.get('Content-Type'), image.content)
            out_path = os.path.join(collection_dir, f'{formatted_number}{ext}')
            with open(out_path, 'wb') as file:
                file.write(image.content)
            print(f"  Image -> [\u2713] (Successfully downloaded as {ext})")
            stats["DownloadedImages"] += 1

print(f"""

Finished downloading collection.


Statistics
-=-=-=-=-=-

Total of {count} units in collection "{CollectionName}".

Downloads:

    JSON Files ->
    {stats["DownloadedData"]} successfully downloaded
    {stats["AlreadyDownloadedData"]} already downloaded

    Images ->
    {stats["DownloadedImages"]} successfully downloaded
    {stats["AlreadyDownloadedImages"]} already downloaded
    {stats["FailedImages"]} failed


You can find the images in the images/{CollectionName} folder.
Images are saved with the correct extension (.png, .svg, .jpg, etc.) for each token.
The JSON for each NFT can be found in the images/{CollectionName}/image_data folder.
Press enter to exit...""")
input()
