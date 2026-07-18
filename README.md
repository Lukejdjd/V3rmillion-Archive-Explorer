# V3rmillion Archive Explorer

A read-only web interface for searching archived threads, posts, and user profiles. The application uses two local SQLite databases with FTS5 indexes and can run directly with Python or as Docker containers.

The database files are release artifacts, not Git source files. They are intentionally excluded by `.gitignore`.

## Current database release

| Artifact | Raw size | Zstandard level 15 | SHA-256 of `.zst` |
|---|---:|---:|---|
| `users.db` | 114,286,592 bytes | 24,486,464 bytes | `9d3f99d83bb379e99c77eeca944ef7cb318a42905adfbb1ac7561ab727156e09` |
| `threads.db` | 6,420,652,032 bytes | 1,389,124,620 bytes | `0c83061ab9eab77a5f66d1de6b074e750a952c9dfc80ac0a3edd5a3af5be5bc9` |

Combined size:

- Queryable SQLite databases: 6,534,938,624 bytes (6.09 GiB)
- Compressed downloads: 1,413,611,084 bytes (1.32 GiB)

Level 15 was measured against level 3 on the release machine. It reduced the
users artifact by 15.88% in 3.1 seconds and the threads artifact by 14.32% in
69 seconds, saving 236,791,735 bytes overall. Decompression remains lossless.

The current release contains 1,028,900 threads, 6,139,739 posts, and 498,730 users. Both databases passed `PRAGMA integrity_check`, and the FTS row counts match their source tables.

## Architecture

The simplest deployment has Oracle serve both the frontend and API:

```text
Visitor -> Cloudflare DNS/proxy -> Oracle VM -> Caddy -> Docker app
                                                       |-> local SQLite databases
                                                       `-> Iframely
                                                backups/downloads -> R2
```

The split deployment keeps static assets on Cloudflare Pages:

```text
Visitor -> Cloudflare Pages -> static HTML/CSS/JavaScript
                    |
                    `-> Pages Function -> api.example.com -> Oracle VM

Oracle VM -> Python API -> local SQLite databases
          `-> Iframely

R2/custom download domain -> users.db.zst, threads.db.zst, SHA256SUMS
```

Start with the single-VM deployment. Add Pages after the site looks correct; it is an optimization, not a prerequisite.

## Run locally with Docker

Requirements:

- Docker Desktop, or Docker Engine with the Compose plugin
- At least 8 GB of free disk space after downloading the compressed files
- `zstd` for decompression

### 1. Obtain the databases

Once a release has been uploaded to R2 or GitHub Releases, use its base download URL.

Windows PowerShell:

```powershell
winget install Meta.Zstandard
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\download_databases.ps1 -BaseUrl "https://downloads.example.com"
```

Linux/macOS:

```bash
sudo apt-get update && sudo apt-get install -y curl zstd
sh scripts/download_databases.sh "https://downloads.example.com"
```

The scripts download `SHA256SUMS`, verify both archives, and create:

```text
data/users.db
data/threads.db
```

If you already have the raw databases, place them at those paths and skip the download step.

### 2. Start the site

Start Docker Desktop (Windows/macOS) or the Docker daemon (Linux), then run:

```bash
docker compose up -d --build archive
```

This starts the core archive and works on both x86-64 and ARM64 (including an
Oracle Ampere A1 VM). Rich URL previews are an optional profile. Enable them
on either architecture with:

```bash
docker compose --profile embeds up -d --build
```

The repository builds [Iframely v2.4.3](https://github.com/itteco/iframely/releases/tag/v2.4.3) from its pinned upstream commit on the
multi-architecture `node:20-bookworm-slim` image. This replaces the previous
x86-only third-party image and uses the same configuration on ARM64 and x86-64.
You may leave the profile disabled when previews are not needed; all archive
search and viewing features remain available.

To regenerate both downloadable database archives at Zstandard level 15 and
verify them before upload, run either:

```powershell
.\scripts\package_databases.ps1
```

```bash
./scripts/package_databases.sh
```

Open <http://localhost:8000/static/search.html>.

Useful commands:

```bash
docker compose ps
docker compose logs -f archive
docker compose logs -f iframely
docker compose down
```

The databases are mounted read-only inside the application container. Iframely is used only for rich link previews; archive search still works if an external preview cannot be fetched.

## Run locally with Python

The web application itself uses only the Python standard library. Python 3.12 or newer is recommended.

By default, `main.py` starts the Iframely Docker service and then starts the web server:

```bash
python main.py
```

Open <http://localhost:8000/static/search.html>.

To run without Docker/Iframely:

PowerShell:

```powershell
$env:MANAGE_IFRAMELY = "0"
python main.py
```

Linux/macOS:

```bash
MANAGE_IFRAMELY=0 python main.py
```

Search and profiles still work; rich external embeds will be unavailable.

Supported server environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `localhost` | Bind address; Docker uses `0.0.0.0` |
| `PORT` | `8000` | HTTP port |
| `ARCHIVE_DATA_DIR` | `./data` | Directory containing both SQLite files |
| `MANAGE_IFRAMELY` | `1` | Start/stop Iframely through Docker Compose |
| `IFRAMELY_BACKEND` | `http://localhost:8061` | Iframely service URL |

## Rebuild the databases from source archives

This is needed only by dataset maintainers. Put `users.zip` and `threads.zip` in `data/`, create a virtual environment, and install parser dependencies:

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

Run users first. On the measured Windows machine, users completed in 8 minutes 38 seconds and threads plus FTS/VACUUM completed in 1 hour 14 minutes 52 seconds.

## Create release downloads

Always compress only after parsing, FTS rebuilding, and `VACUUM` have completed.

```bash
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

The `.zst` files are lossless. Ordinary SQLite cannot query them while compressed; local users must decompress them first.

## Publish database artifacts to Cloudflare R2

R2 is artifact and backup storage, not the live SQLite filesystem. The Oracle VM must query raw databases on its local block volume.

1. In Cloudflare, create an R2 Standard bucket such as `archive-databases`.
2. Create an R2 API token limited to that bucket.
3. Configure `rclone` or the AWS CLI using the R2 credentials.
4. Upload all three release files.

Rclone is recommended for the 1.39 GB thread artifact because it supports resumable multipart uploads:

```bash
rclone copy data/downloads/ r2:archive-databases/ --progress
rclone ls r2:archive-databases/
```

AWS CLI alternative:

```bash
aws s3 cp data/downloads/ s3://archive-databases/ \
  --recursive \
  --endpoint-url "https://ACCOUNT_ID.r2.cloudflarestorage.com"
```

Connect a custom domain such as `downloads.example.com` to the bucket and make only the release objects public. The download scripts use this hostname as `BaseUrl`.

Relevant Cloudflare documentation:

- [R2 CLI tools](https://developers.cloudflare.com/r2/get-started/cli/)
- [R2 multipart uploads](https://developers.cloudflare.com/r2/objects/upload-objects/)
- [R2 public buckets and custom domains](https://developers.cloudflare.com/r2/buckets/public-buckets/)

## Deploy the API and optional full site to Oracle Cloud

### 1. Create the VM

Create an Always Free eligible Ubuntu Ampere A1 VM in the account's home region. A configuration around 2 OCPUs and 12 GB memory is appropriate when it is shown as Always Free eligible. Allocate enough boot/block storage for the repository, 6.1 GiB of live databases, compressed downloads, Docker images, and temporary upgrades; 40 GB or more is comfortable.

Add ingress rules for:

- TCP 22 from your own IP for SSH
- TCP 80 from the internet
- TCP 443 from the internet

Do not expose ports 8000 or 8061 publicly.

### 2. Install Docker and clone the repository

Follow Docker's official Ubuntu installation instructions, then:

```bash
git clone https://github.com/OWNER/V3rmillion-Archive-Explorer.git
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
sh scripts/download_databases.sh "https://downloads.example.com"
```

### 3. Start the production stack

```bash
docker compose -f docker-compose.yml -f compose.prod.yml --profile embeds up -d --build
docker compose -f docker-compose.yml -f compose.prod.yml ps
```

The production override adds Caddy on ports 80/443. Caddy obtains and renews TLS certificates and proxies requests to the archive container.

### 4. Configure Cloudflare DNS

Create an `A` record:

```text
api.example.com -> ORACLE_VM_PUBLIC_IP
```

Enable the Cloudflare proxy after the origin responds. Set Cloudflare SSL/TLS mode to **Full (strict)** once Caddy has obtained its certificate.

The complete site can now be tested at:

```text
https://api.example.com/static/search.html
```

This is a valid production setup by itself. Pages is optional.

## Put static files on Cloudflare Pages

The repository includes a Pages build script and Functions that proxy browser API requests to Oracle. The browser continues using relative `/api/*` URLs, so no frontend rewrite is needed.

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

6. Deploy and attach the public hostname, for example `archive.example.com`.

The Pages Functions are:

- `functions/api/[[path]].js` for `/api/*`
- `functions/iframely.js` for `/iframely`

Pages routing and build references:

- [Pages Functions routing](https://developers.cloudflare.com/pages/functions/routing/)
- [Pages build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/)

## Backups and updates

The production databases are mounted read-only, so the uploaded release artifacts are also the primary restore source. Keep at least two R2 prefixes:

```text
releases/2026-07-18/users.db.zst
releases/2026-07-18/threads.db.zst
releases/2026-07-18/SHA256SUMS
releases/latest/...
```

For an update:

1. Rebuild and validate both databases on a workstation.
2. Compress them with Zstandard level 15.
3. Test both streams with `zstd -t` and generate `SHA256SUMS`.
4. Upload to a new versioned R2 prefix.
5. Download/decompress on Oracle into a staging directory.
6. Verify `PRAGMA integrity_check`.
7. Stop the archive container, atomically replace both database files, and restart it.
8. Update the `latest` R2 objects only after production validation.

Never edit SQLite directly over an R2/network filesystem.

## GitHub open-source release

Do not commit raw or compressed databases to Git. Publish the source code normally and attach these files to a GitHub Release or link to R2:

- `users.db.zst`
- `threads.db.zst`
- `SHA256SUMS`

GitHub currently requires each release asset to be under 2 GiB. The 1.39 GB thread artifact fits, but R2 should remain the canonical mirror because it provides resumable uploads and a stable custom download domain. See [GitHub's release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

After installing and signing in to the GitHub CLI, create a database release:

```bash
gh auth login
gh release create database-2026-07-18 \
  data/downloads/users.db.zst \
  data/downloads/threads.db.zst \
  data/downloads/SHA256SUMS \
  --title "Database snapshot 2026-07-18" \
  --notes "Lossless Zstandard level 15 archive database snapshot. Verify with SHA256SUMS."
```

Verify that all three assets are attached:

```bash
gh release view database-2026-07-18
```

People can then install the databases directly from that release. Replace
`OWNER` and `REPOSITORY` below with the GitHub repository path:

```powershell
.\scripts\download_databases.ps1 -BaseUrl "https://github.com/OWNER/REPOSITORY/releases/download/database-2026-07-18"
```

```bash
sh scripts/download_databases.sh "https://github.com/OWNER/REPOSITORY/releases/download/database-2026-07-18"
```

Do not use Git LFS or commit the database files themselves. A release asset is
kept outside Git history, so code clones remain small. If `threads.db.zst`
eventually reaches 2 GiB, publish that snapshot through R2 instead.

Before publishing the dataset, separately review copyright, privacy, takedown, and data-retention obligations. An open-source code license does not automatically grant permission to redistribute archived user content.

## Troubleshooting

### `threads.db not found` or `users.db not found`

Confirm both files exist directly under `data/`, not only under `data/downloads/`:

```bash
ls -lh data/users.db data/threads.db
```

### Docker site is not reachable

```bash
docker compose ps
docker compose logs archive
```

The local listener is intentionally bound to `127.0.0.1:8000`. Use Caddy for public production traffic.

### Iframely errors

Search remains functional. Check:

```bash
docker compose logs iframely
```

### Validate databases manually

```bash
sqlite3 data/users.db "PRAGMA integrity_check;"
sqlite3 data/threads.db "PRAGMA integrity_check;"
```

Both commands should print `ok`.
