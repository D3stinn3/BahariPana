import requests
import os
import json
import math
from random_user_agent.user_agent import UserAgent
from random_user_agent.params import SoftwareName, OperatingSystem
import cloudscraper
import argparse
import aiohttp
import asyncio


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

API_KEY = '795d349c0bea4fa19a71127d4554ec44'

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
async def ipfs_resolve(session,image_url):
    cid = image_url.removeprefix("https://ipfs.io/ipfs/")
    for gateway in ipfs_gateways:
        request = requests.get(f"https://{gateway}/ipfs/{cid}")
        if request.status_code == 200:
            break
    return request

async def download_image(session, image_url):
    async with session.get(image_url) as response:
        if response.status == 200:
            content = await response.read()
            with open(f"./images/{CollectionName}/{formatted_number}.png", "wb+") as file:
                file.write(content)


# Iterate through every unit
async def download_nfts():
    async with aiohttp.ClientSession() as session:
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

                    # Check if image already exists, if it does, skip saving it
                    if os.path.exists(f'./images/{CollectionName}/{formatted_number}.png'):
                        print(f"  Image -> [\u2713] (Already Downloaded)")
                        stats["AlreadyDownloadedImages"] += 1
                        continue
                    else:
                        # Make the request to the URL to get the image
                        if not asset["image_url"] is None:
                            image_url = asset["image_url"]
                        elif not asset["image_url"] is None:
                            image_url = asset["image_url"]
                        else:
                            image_url = ""

                        if not len(image_url) == 0:
                            image = requests.get(image_url)
                        else:
                            print(f"  Image -> [!] (Blank URL)")
                            stats["FailedImages"] += 1
                            continue

                    # Replacement
                    if image_url.startswith("https://ipfs.io/ipfs/"):
                        image_data = await ipfs_resolve(session, image_url)
                        if image_data:
                            with open(f"./images/{CollectionName}/{formatted_number}.png", "+wb") as image_file:
                                image_file.write(image_data)
                            print(f" Image -> [\u2713] (Successfully downloaded)")
                            stats["DownloadedImages"] += 1

                        else:
                            print(f" Image -> [!] (Failed to download from IPFS)")
                            stats["FailedImages"] += 1
                            continue

                    else:
                        # Download image asynchronously
                        tasks.append(download_image(session, image_url))

                await asyncio.gather(*tasks)

asyncio.run(download_nfts())



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
The JSON for each NFT can be found in the images/{CollectionName}/image_data folder.
Press enter to exit...""")
input()
