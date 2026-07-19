# V3rmillion Archive Explorer

A read-only web interface for searching archived threads, posts, and user profiles. The application uses two local SQLite databases with FTS5 indexes and can run directly with Python or as Docker containers.

## Credits

[64kun/V3rmillion-Archive-Explorer](https://github.com/64kun/V3rmillion-Archive-Explorer) provided a great foundation for understanding how to parse the archived data.

## Source archives

To rebuild the databases yourself, download `users.zip` and `threads.zip` from the [V3rmillion archive on Internet Archive](https://archive.org/download/v3rmillion), then place both files in the `data/` directory before running the parsers.

## Current database release

| Artifact | Raw size | Zstandard level 15 | SHA-256 of `.zst` |
|---|---:|---:|---|
| `users.db` | 114,286,592 bytes | 24,486,464 bytes | `9d3f99d83bb379e99c77eeca944ef7cb318a42905adfbb1ac7561ab727156e09` |
| `threads.db` | 6,420,652,032 bytes | 1,389,124,620 bytes | `0c83061ab9eab77a5f66d1de6b074e750a952c9dfc80ac0a3edd5a3af5be5bc9` |

Combined size:

- Queryable SQLite databases: 6,534,938,624 bytes (6.09 GiB)
- Compressed downloads: 1,413,611,084 bytes (1.32 GiB)



## Architecture

Single-VM deployment:

```text
Visitor -> Cloudflare DNS/proxy -> Oracle VM -> Caddy (HTTPS) -> FastAPI/Uvicorn
                                                       |-> local SQLite FTS databases
                                                       `-> Iframely

GitHub Release -> versioned .zst snapshot -> Oracle VM
```

Split deployment with static assets on Cloudflare Pages:

```text
Visitor -> Cloudflare Pages -> static HTML/CSS/JavaScript
                    |
                    `-> Pages Function -> api.example.com -> Caddy -> FastAPI

Oracle VM -> FastAPI (Uvicorn workers) -> local SQLite FTS databases
          `-> Iframely (optional)

GitHub Release -> users.db.zst, threads.db.zst, SHA256SUMS
```

Target on Always Free (2 OCPU / 12 GB): hundreds of thousands of requests/day
with rate limits, pagination, and response caching. Scrapers and uncached heavy
searches are the main risk — not normal browsing.

## Run locally with Docker

Requirements:

- Docker Desktop, or Docker Engine with the Compose plugin
- At least 8 GB of free disk space after downloading the compressed files
- `zstd` for decompression

### 1. Obtain the databases

Open the repository's [Releases page](https://github.com/Lukejdjd/V3rmillion-Archive-Explorer/releases) and use the newest database snapshot. Copy its release tag and substitute it for `<RELEASE_TAG>` in the commands below.

Windows PowerShell:

```powershell
winget install Meta.Zstandard
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\download_databases.ps1 -BaseUrl "https://github.com/Lukejdjd/V3rmillion-Archive-Explorer/releases/download/<RELEASE_TAG>"
```

Linux/macOS:

```bash
sudo apt-get update && sudo apt-get install -y curl zstd
sh scripts/download_databases.sh "https://github.com/Lukejdjd/V3rmillion-Archive-Explorer/releases/download/<RELEASE_TAG>"
```

The scripts download `SHA256SUMS`, verify both archives, and create:

```text
data/users.db
data/threads.db
```

### 2. Start the site

Start Docker Desktop (Windows/macOS) or the Docker daemon (Linux), then run:

```bash
docker compose up -d --build archive
```

Rich URL previews are optional. Enable them with:

```bash
docker compose --profile embeds up -d --build
```

The repository builds [Iframely v2.4.3](https://github.com/itteco/iframely/releases/tag/v2.4.3) from its pinned upstream commit on the multi-architecture `node:20-bookworm-slim` image, supporting both ARM64 and x86-64.

Open <http://localhost:8000/static/search.html>.

Useful commands:

```bash
docker compose ps
docker compose logs -f archive
docker compose logs -f iframely
docker compose down
```

## Quick local test (Windows)

Databases must already be at `data/users.db` and `data/threads.db`.

```powershell
pip install -r requirements.txt
$env:MANAGE_IFRAMELY = "0"
$env:WEB_CONCURRENCY = "1"
python main.py
```

Then open <http://localhost:8000/static/search.html> and try a thread search.
Health check: <http://localhost:8000/healthz>

Docker alternative (rebuilds with FastAPI baked into the image):

```powershell
docker compose up -d --build archive
```

## Run locally with Python

The API runs on FastAPI + Uvicorn. Prefer a venv for a clean install:

```bash
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MANAGE_IFRAMELY = "0"
python main.py
```

Linux/macOS:

```bash
. .venv/bin/activate
pip install -r requirements.txt
MANAGE_IFRAMELY=0 python main.py
```

Or run Uvicorn directly:

```bash
WEB_CONCURRENCY=2 uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000/static/search.html>.

Supported server environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `localhost` | Bind address; Docker uses `0.0.0.0` |
| `PORT` | `8000` | HTTP port |
| `WEB_CONCURRENCY` | `2` | Uvicorn worker processes |
| `ARCHIVE_DATA_DIR` | `./data` | Directory containing both SQLite files |
| `MANAGE_IFRAMELY` | `1` | Start/stop Iframely through Docker Compose |
| `IFRAMELY_BACKEND` | `http://localhost:8061` | Iframely service URL |
| `SEARCH_RATE_LIMIT` | `10` | Searches/minute/IP without Turnstile |
| `READ_RATE_LIMIT` | `60` | Thread/profile reads/minute/IP |
| `TURNSTILE_SOFT_LIMIT` | `10` | Searches/minute before Turnstile challenge |
| `BLOCK_HARD_LIMIT` | `100` | Searches/minute before temporary IP block |
| `TURNSTILE_SITE_KEY` | _(empty)_ | Cloudflare Turnstile site key |
| `TURNSTILE_SECRET_KEY` | _(empty)_ | Cloudflare Turnstile secret (never commit) |

## Rebuild the databases from source archives

After downloading `users.zip` and `threads.zip`, create a virtual environment and install the parser dependencies:

```bash
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-parser.txt
python scripts\profile_parser.py --source zip --overwrite
python scripts\thread_parser.py --source zip --overwrite
```

Linux/macOS:

```bash
. .venv/bin/activate
pip install -r requirements-parser.txt
python scripts/profile_parser.py --source zip --overwrite
python scripts/thread_parser.py --source zip --overwrite
```

Run users first. Expect parsing to take a while.

## Create release downloads

Always compress only after parsing, FTS rebuilding, index optimization, and `VACUUM` have completed.

```bash
python scripts/optimize_indexes.py
mkdir -p data/downloads
zstd -15 -T0 -f -o data/downloads/users.db.zst -- data/users.db
zstd -15 -T0 -f -o data/downloads/threads.db.zst -- data/threads.db
zstd -t data/downloads/users.db.zst
zstd -t data/downloads/threads.db.zst
```

Create the checksum manifest:

Linux:

```bash
cd data/downloads
sha256sum users.db.zst threads.db.zst > SHA256SUMS
```

PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 data\downloads\users.db.zst
Get-FileHash -Algorithm SHA256 data\downloads\threads.db.zst
```

## Optional Cloudflare R2 mirror

GitHub Releases holds the versioned compressed snapshots. Consider adding an R2 mirror only when:

- an individual release artifact approaches GitHub's 2 GiB limit
- public downloads need a custom hostname without GitHub authentication
- automated rotating backups are needed more often than versioned releases



## Deploy to Oracle Cloud

### 1. Create the VM

Create an Always Free eligible Ubuntu Ampere A1 VM in the account's home region. A configuration around 2 OCPUs and 12 GB memory works well. Eligible shapes may not be available in every region — try another availability domain or run the archive locally instead.

Allocate at least 40 GB of storage for the repository, databases, Docker images, and working room.

Add ingress rules for:

- TCP 22 from your own IP for SSH
- TCP 80 from the internet
- TCP 443 from the internet

Do not expose ports 8000 or 8061 publicly.

### 2. Install Docker and clone the repository

Follow Docker's official Ubuntu installation instructions, then:

```bash
git clone https://github.com/Lukejdjd/V3rmillion-Archive-Explorer.git
cd V3rmillion-Archive-Explorer
cp .env.example .env
```

Edit `.env`:

```dotenv
SITE_DOMAIN=api.example.com
```

Download and decompress the databases directly on the VM:

```bash
sudo apt-get install -y curl zstd
sh scripts/download_databases.sh "https://github.com/Lukejdjd/V3rmillion-Archive-Explorer/releases/download/<RELEASE_TAG>"
```

### 3. Start the production stack

```bash
docker compose -f docker-compose.yml -f compose.prod.yml --profile embeds up -d --build
docker compose -f docker-compose.yml -f compose.prod.yml ps
```

Caddy handles ports 80/443, obtains and renews TLS certificates automatically, and reverse-proxies to FastAPI (bound to localhost only). See `deploy/oracle-hardening.md` for firewall, SSH keys, backups, and secrets.

Optional monitoring (localhost only — SSH tunnel or Cloudflare Tunnel):

```bash
docker compose -f docker-compose.monitoring.yml up -d
```

- Netdata: <http://127.0.0.1:19999>
- Uptime Kuma: <http://127.0.0.1:3001>

### 4. Configure Cloudflare DNS

Create an `A` record:

```text
api.example.com -> ORACLE_VM_PUBLIC_IP
```

Enable the Cloudflare proxy after the origin responds. Set SSL/TLS mode to **Full (strict)** once Caddy has obtained its certificate.

## Put static files on Cloudflare Pages

1. Connect the GitHub repository to Cloudflare Pages.
2. Use no framework preset.
3. Set the build command to:

   ```bash
   sh deploy/build-pages.sh
   ```

4. Set the build output directory to:

   ```text
   dist
   ```

5. Add a Pages environment variable:

   ```text
   API_ORIGIN=https://api.example.com
   ```

6. Optional bot protection: create a Cloudflare Turnstile widget, then set
   `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` in the Oracle `.env` and
   redeploy the API. Challenges only appear for high search rates.

7. Deploy and attach the public hostname, for example `archive.example.com`.

After rebuilding databases from source, run index optimization before packaging:

```bash
python scripts/optimize_indexes.py
```

The Pages Functions are:

- `functions/api/[[path]].js` for `/api/*`
- `functions/iframely.js` for `/iframely`

Pages routing and build references:

- [Pages Functions routing](https://developers.cloudflare.com/pages/functions/routing/)
- [Pages build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/)

## Backups and updates

Keep the current release, at least one older release, and a separate local copy of the compressed files.

```text
database-<date>
|-- users.db.zst
|-- threads.db.zst
`-- SHA256SUMS
```

For an update:

1. Rebuild and validate both databases on a workstation.
2. Compress with Zstandard level 15.
3. Test both streams with `zstd -t` and generate `SHA256SUMS`.
4. Upload all three files to a new versioned GitHub Release.
5. Download and decompress on Oracle into a staging directory.
6. Verify `PRAGMA integrity_check`.
7. Stop the archive container, replace both database files, and restart it.
8. Keep the previous release until the new databases are validated.

## Publish database artifacts with GitHub Releases

Attach these files to each GitHub Release:

- `users.db.zst`
- `threads.db.zst`
- `SHA256SUMS`

After installing and signing in to the GitHub CLI:

```bash
gh auth login
gh release create database-YYYY-MM-DD \
  data/downloads/users.db.zst \
  data/downloads/threads.db.zst \
  data/downloads/SHA256SUMS \
  --title "Database snapshot YYYY-MM-DD" \
  --notes "Lossless Zstandard level 15 archive database snapshot. Verify with SHA256SUMS."
```

Verify that all three assets are attached:

```bash
gh release view database-YYYY-MM-DD
```

The archived content belongs to its original authors. Review V3rmillion's terms before redistributing.

## Troubleshooting

### `threads.db not found` or `users.db not found`

Confirm both files exist directly under `data/`:

```bash
ls -lh data/users.db data/threads.db
```

### Docker site is not reachable

```bash
docker compose ps
docker compose logs archive
```

The local listener is bound to `127.0.0.1:8000`. Use Caddy for public traffic.

### Iframely errors

Search remains functional without Iframely. Check:

```bash
docker compose logs iframely
```

### Validate databases manually

```bash
sqlite3 data/users.db "PRAGMA integrity_check;"
sqlite3 data/threads.db "PRAGMA integrity_check;"
```

Both commands should print `ok`.