# BahariPana
The Webscrapper

## How to use

Create a virtual environment, then:

```bash
pip install -r requirements.txt
python app.py download {collection_slug}
```

`python app.py {collection_slug}` still works and runs **download** mode (backward compatible).

Examples:

```bash
python app.py normies
python app.py download degods-eth
python app.py repair normies --dry-run
python app.py repair normies --ids 21,22,23
```

## Download mode

Full collection download with cursor pagination ([OpenSea API v2](https://docs.opensea.io/reference/get_nfts_by_collection)).

| Flag | Default | Description |
|------|---------|-------------|
| `--page-size` | `50` | NFTs per OpenSea API request (1–200) |
| `--delay` | `0.35` | Seconds after each image HTTP request |
| `--page-delay` | `1.5` | Seconds between OpenSea list pages |
| `--max-retries` | `5` | Retries for transient network / HTTP errors |

```bash
python app.py download normies --page-size 50 --delay 0.35 --page-delay 1.5
```

## Repair mode

Fill **gaps only** — missing JSON and/or images — without re-scanning the entire collection.

Uses `POST /api/v2/nfts/batch` for efficient lookups, with single-NFT fallback for IDs the batch omits.

| Flag | Description |
|------|-------------|
| `--dry-run` | List missing token IDs; no downloads |
| `--json-only` | Only missing `image_data/{id}.json` files |
| `--images-only` | Only images where JSON exists but the image file is missing |
| `--ids 21,22,23` | Repair specific token IDs |
| `--from-file gaps.txt` | One token ID per line |
| `--min-id` / `--max-id` | Limit gap scan range (default: `0` … `total_supply - 1`) |
| `--batch-size` | `30` | NFTs per batch API request (1–50) |
| `--delay` | Same as download — after each image request |
| `--page-delay` | Delay between batch API requests |

```bash
# See how many files are missing
python app.py repair normies --dry-run

# Fix all gaps (JSON + images)
python app.py repair normies

# Fix only missing metadata
python app.py repair normies --json-only

# Fix screenshot gaps only
python app.py repair normies --ids 21,22,23
```

Tokens not returned by OpenSea are logged to `images/{collection}/repair_not_found.json`.

## Resume after interruption

- **Download:** Re-run the same download command; existing files are skipped.
- **Repair:** Re-run repair; only remaining gaps are processed.

## Image formats

Each NFT image is saved with the correct extension (`.png`, `.svg`, `.jpg`, etc.). Collections such as **normies** use SVG; **degods-eth** uses PNG. Windows Paint opens raster formats; open SVG in a browser or vector editor.

## API key

Requires an OpenSea API key in `X-API-KEY` (configured in `app.py`).
