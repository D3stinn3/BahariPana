# BahariPana
The Webscrapper

## How to use

Create a virtual environment, then:

```bash
pip install -r requirements.txt
python app.py {collection_slug}
```

Example:

```bash
python app.py normies
python app.py degods-eth
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--page-size` | `50` | NFTs per OpenSea API request (1–200). Lower values are gentler on rate limits. |
| `--delay` | `0.35` | Seconds to wait after each image HTTP request. |
| `--page-delay` | `1.5` | Seconds to wait between OpenSea list pages. |
| `--max-retries` | `5` | Retry attempts for transient network or HTTP 429/5xx errors. |

Large collections (for example **normies**, ~10k tokens):

```bash
python app.py normies --page-size 50 --delay 0.35 --page-delay 1.5
```

## Resume after interruption

The script skips JSON and image files that already exist. If a run stops due to a network error, run the same command again; it continues where it left off.

## Image formats

Each NFT image is saved with the correct file extension based on the source URL and content (for example `.png` or `.svg`). Collections such as **normies** use SVG artwork; collections such as **degods-eth** use PNG. Windows Paint opens raster formats (PNG, JPG); open SVG files in a browser or vector editor.

## OpenSea API

Uses [OpenSea API v2](https://docs.opensea.io/reference/get_nfts_by_collection) cursor pagination (`limit` + `next`). Requires an API key in `X-API-KEY` (configured in `app.py`).
