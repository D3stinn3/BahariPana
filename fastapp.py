import asyncio
import aiohttp
import os
import json
import math
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

API_KEY = '795d349c0bea4fa19a71127d4554ec44'

# Headers for the request. Currently this is generating random user agents
# Use a custom header version here -> https://www.whatismybrowser.com/guides/the-latest-user-agent/
headers = {
    'User-Agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_2_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
    'X-API-KEY': API_KEY
}

# Get information regarding collection
async def fetch_collection_info(session, collection_name):
    url = f"http://api.opensea.io/api/v2/collections/{collection_name}?format=json"
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def download_collection_info(collection_name):
    async with aiohttp.ClientSession() as session:
        collection_info = await fetch_collection_info(session, collection_name)
        return collection_info

async def download_assets(session, collection_name, offset):
    limit = f"limit=200&offset={offset}"
    url = f"https://api.opensea.io/api/v2/collection/{collection_name}/nfts?{limit}"
    async with session.get(url, headers=headers) as response:
        data = await response.json()
        return data.get('nfts', [])

async def download_and_process_assets(collection_name, count):
    tasks = []
    async with aiohttp.ClientSession() as session:
        for offset in range(0, count, 200):
            tasks.append(download_assets(session, collection_name, offset))
        results = await asyncio.gather(*tasks)
        assets = [asset for sublist in results for asset in sublist]
        return assets
    
# If the metadata is desired then replace code with the snippet
    """
    async def download_and_process_assets(collection_name, count):
    directory = os.path.join('images', collection_name, 'image_data')
    os.makedirs(directory, exist_ok=True)

    tasks = []
    async with aiohttp.ClientSession() as session:
        for offset in range(0, count, 200):
            tasks.append(download_assets(session, collection_name, offset))

        results = await asyncio.gather(*tasks)
        assets = [asset for sublist in results for asset in sublist]

        # Download JSON files
        for asset in assets:
            formatted_number = f"{asset['identifier']:05}"
            json_file_path = os.path.join(directory, f"{formatted_number}.json")
            if not os.path.exists(json_file_path):
                with open(json_file_path, 'w') as json_file:
                    json.dump(asset, json_file, indent=4)

        return assets
    """

async def download_image(session, image_url, file_path):
    async with session.get(image_url) as response:
        if response.status == 200:
            with open(file_path, 'wb') as f:
                f.write(await response.read())

async def download_images(collection_name, assets):
    # Create directory if it doesn't exist
    directory = os.path.join('images', collection_name)
    os.makedirs(directory, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        tasks = []
        for asset in assets:
            if not asset.get('image_url'):
                continue
            image_url = asset['image_url']
            filename = os.path.join(directory, f"{asset['identifier']}.png")
            tasks.append(download_image(session, image_url, filename))
        await asyncio.gather(*tasks)

async def main():
    collection_info = await download_collection_info(CollectionName)
    count = int(collection_info.get("total_supply", 0))
    if not count:
        print("No collection found.")
        return

    print(f"Beginning download of \"{CollectionName}\" collection.\n")
    
    assets = await download_and_process_assets(CollectionName, count)
    await download_images(CollectionName, assets)

    print("Finished downloading collection.\n")
    print("You can find the images in the images/{CollectionName} folder.")

if __name__ == "__main__":
    asyncio.run(main())