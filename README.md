# BSOS Wiki Stack

A fully private Wiki.js deployment for battlefield shock research, bundled with an OpenAI-powered indexing pipeline and a lightweight “Ask Wiki” interface. This README walks through the entire server-side setup so you can re-create or adapt the stack for additional wikis.

---

## Table of Contents

1. [Architecture at a Glance](#architecture-at-a-glance)  
2. [Prerequisites](#prerequisites)  
3. [Base System Setup](#base-system-setup)  
4. [Directory Layout](#directory-layout)  
5. [Install Docker & Compose](#install-docker--compose)  
6. [Caddy Reverse Proxy](#caddy-reverse-proxy)  
7. [Docker Compose Stack](#docker-compose-stack)  
8. [Wiki.js Initial Setup](#wikijs-initial-setup)  
9. [Git-Backed Content Storage](#git-backed-content-storage)  
10. [Locking Down Access (Google OAuth)](#locking-down-access-google-oauth)  
11. [Uploads Directory](#uploads-directory)  
12. [Indexer + Vector Store Pipeline](#indexer--vector-store-pipeline)  
13. [Client Ingest (Laptop Scripts)](#client-ingest-laptop-scripts)  
14. [“Ask Wiki” API + UI](#ask-wiki-api--ui)  
15. [Multi-Wiki Pattern](#multi-wiki-pattern)  
16. [Common Commands](#common-commands)  

---

## Architecture at a Glance

- **Wiki.js** (Docker) serves the private Markdown wiki, backed by **PostgreSQL** and **Elasticsearch**.  
- **Git** stores wiki content under `/opt/bsos-wiki-data/repo`.  
- **Caddy** terminates TLS for `https://bsos.wiki`, proxies Wiki.js, exposes uploads, and fronts the Ask API/UI.  
- **Indexer + Ask API** (`/opt/bsos-wiki-indexer`) ingest Markdown (and later PDFs) into an **OpenAI vector store**, then provide a FastAPI endpoint + static UI for NotebookLM-style queries.  
- **State files** (`.indexer_state.json`, `file_index.yaml`) record document hashes and vector-store metadata for citations and future organizer agents.

---

## Prerequisites

- Ubuntu 24.04 LTS droplet (DigitalOcean or equivalent).  
- DNS `A` record pointing `bsos.wiki → <droplet IP>`; leave Cloudflare in “DNS only” or ensure HTTP/HTTPS is allowed for Let’s Encrypt.  
- SSH access as `root` (or sudo-capable user).

---

## Base System Setup

```bash
ssh root@YOUR_DROPLET_IP

apt update && apt upgrade -y
apt install -y git curl ufw

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

---

## Directory Layout

```text
/opt/
  bsos-wiki-stack/      # docker-compose.yml, README, .env
  bsos-wiki-data/       # git repo + uploads + ask UI
    repo/
    uploads/
    ask/
  bsos-wiki-indexer/    # indexer + Ask API source
```

Create them upfront:

```bash
mkdir -p /opt/bsos-wiki-stack
mkdir -p /opt/bsos-wiki-data/{repo,uploads,ask}
mkdir -p /opt/bsos-wiki-indexer
```

Duplicate this pattern for new wikis (e.g., `/opt/second-wiki-stack`, etc.).

---

## Install Docker & Compose

```bash
curl -fsSL https://get.docker.com | bash
usermod -aG docker root

curl -L \
  "https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-$(uname -s)-$(uname -m)" \
  -o /usr/local/bin/docker-compose

chmod +x /usr/local/bin/docker-compose
docker-compose version
```

---

## Caddy Reverse Proxy

Install Caddy:

```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list

apt update && apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```caddyfile
{
    email you@example.com
}

bsos.wiki {
    handle_path /ask/api/* {
        reverse_proxy localhost:4000
    }

    handle_path /ask/* {
        root * /opt/bsos-wiki-data/ask
        file_server
    }

    handle_path /uploads/* {
        root * /opt/bsos-wiki-data/uploads
        file_server
    }

    reverse_proxy localhost:3000   # Wiki.js
}
```

Reload:

```bash
systemctl reload caddy
```

---

## Docker Compose Stack

`/opt/bsos-wiki-stack/docker-compose.yml`:

```yaml
version: "3.9"

services:
  db:
    image: postgres:14
    container_name: bsos-wiki-db
    environment:
      POSTGRES_DB: wiki
      POSTGRES_USER: wikijs
      POSTGRES_PASSWORD: wikijs123   # change me
    volumes:
      - dbdata:/var/lib/postgresql/data
    restart: always
    networks: [wiki_net]

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:7.17.21
    container_name: bsos-wiki-es
    environment:
      - discovery.type=single-node
      - ES_JAVA_OPTS=-Xms1g -Xmx1g
      - xpack.security.enabled=false
    ulimits:
      memlock: { soft: -1, hard: -1 }
    volumes:
      - esdata:/usr/share/elasticsearch/data
    restart: always
    networks: [wiki_net]

  wikijs:
    image: requarks/wiki:latest
    container_name: bsos-wiki
    depends_on: [db, elasticsearch]
    environment:
      DB_TYPE: postgres
      DB_HOST: db
      DB_PORT: 5432
      DB_USER: wikijs
      DB_PASS: wikijs123
      DB_NAME: wiki
      ES_HOST: http://elasticsearch:9200
    ports:
      - "3000:3000"
    restart: always
    networks: [wiki_net]
    volumes:
      - /opt/bsos-wiki-data:/wiki/data

  bsos-ask:
    build: { context: /opt/bsos-wiki-indexer }
    container_name: bsos-ask
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    ports:
      - "4000:4000"
    restart: always
    networks: [wiki_net]

volumes:
  dbdata:
  esdata:

networks:
  wiki_net:
    driver: bridge
```

`.env` beside it:

```bash
OPENAI_API_KEY=sk-your-key-here
```

Bring the stack up:

```bash
cd /opt/bsos-wiki-stack
docker-compose build
docker-compose up -d
```

---

## Wiki.js Initial Setup

Visit `https://bsos.wiki` and complete the installer:

1. Create an admin account.  
2. Confirm DB settings (already pointed at the Postgres container).  
3. Finish wizard.

---

## Git-Backed Content Storage

- The Wiki.js container writes to `/wiki/data`, mapped to `/opt/bsos-wiki-data`.  
- Configure Git storage inside Wiki.js (Administration → Storage → Git):
  - Repository URL: `git@github.com:YOUR_USER/bsos-wiki-content.git`  
  - Auth: SSH (paste a key that has repo access)  
  - Local path: `/wiki/data/repo`  
  - Mode: Push-to-target or bi-directional  
  - Sync schedule: default  
- After syncing, the host contains `/opt/bsos-wiki-data/repo`.

---

## Locking Down Access (Google OAuth)

1. In Google Cloud Console, create OAuth credentials with redirect URL `https://bsos.wiki/login/google/callback`.  
2. In Wiki.js → Administration → Authentication → Google:
   - Enter client ID/secret.  
   - Enable auto-register and restrict domains if desired.  
3. Remove all Guest read permissions so anonymous visitors see only the login screen.

---

## Uploads Directory

```bash
mkdir -p /opt/bsos-wiki-data/uploads
chown -R 1000:1000 /opt/bsos-wiki-data
chmod 755 /opt/bsos-wiki-data/uploads
```

Caddy already exposes `/uploads/*` to this folder, so `/opt/bsos-wiki-data/uploads/paper.pdf` is available at `https://bsos.wiki/uploads/paper.pdf`.

---

## Indexer + Vector Store Pipeline

All indexer code lives in `/opt/bsos-wiki-indexer`.

### Python Environment

```bash
cd /opt/bsos-wiki-indexer
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

`.env` (local dev):

```env
OPENAI_API_KEY=sk-your-key-here
```

### Config

`config.yaml`:

```yaml
wiki_repo_root: "/opt/bsos-wiki-data/repo"

openai:
  vector_store_id: "vs_TBD"
```

### Vector Store Creation

`create_vector_store.py`:

```python
#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

resp = client.vector_stores.create(name="bsos-wiki-store")
print("Vector store ID:", resp.id)
```

Copy the returned ID into `config.yaml`.

### Indexer Highlights (`indexer_stub.py`)

- Walks Markdown files under `wiki_repo_root`.  
- Reads front matter (`doc_id`, `title`, `tags`, etc.).  
- Builds `WikiDocument` objects containing:
  - `doc_id` (stable identifier, fallback slug + warning if missing)  
  - `kind` (“wiki” now; future PDF ingestion uses “pdf”)  
  - `content` prefixed with `Kind:` and `Source:` lines so the model can cite properly  
- Uploads to the vector store (new API first, legacy fallback if needed).  
- Saves two local state files:  
  - `.indexer_state.json` → `doc_id` → content hash (skip unchanged docs)  
  - `file_index.yaml` → `file_id` → `{doc_id, kind, wiki_path, wiki_url, title, tags, ...}`  

Run:

```bash
python indexer_stub.py
```

This primes the vector store and produces `file_index.yaml` for downstream citation parsing and future organizer agents.

---

## Client Ingest (Laptop Scripts)

The ingestion toolkit that turns raw PDFs into wiki-ready Markdown now ships inside this repo under `client_ingest/`, but it is **meant to be run from your laptop**, not the droplet. Highlights:

- `ingest_paper.py` summarizes a single PDF, uploads the source file (optional), and writes Markdown with front matter that already includes `doc_id` + `kind: "wiki"` so the droplet indexer and `file_index.yaml` stay in sync.
- `batch_ingest.py` walks entire folders of PDFs, forwarding every flag after `--` directly to `ingest_paper.py` and tracking successes in `batch_ingested.txt`.
- `client_ingest/README.md` documents the workflow (prereqs, OCR fallback, token/cost tracking, the new `--doc-id` override, etc.).
- The folder has its own `.gitignore` so local state such as `batch_ingested.txt`, `failed_pdfs.txt`, and `ingest.log` never pollute the server-side repo.

Keep this directory intact for source control, but run the commands from your laptop checkout where you have direct access to the PDFs and GUI tools; only the resulting Markdown commits (and optional PDF uploads) need to reach the droplet.

---

## “Ask Wiki” API + UI

All source lives in `/opt/bsos-wiki-indexer` and is containerized as `bsos-ask`.

### API (FastAPI)

- `ask_api.py` loads `config.yaml`, system prompt, and the vector store ID.  
- POST `/ask` takes `{ "query": "..." }`, calls `client.responses.create(...)` with `file_search`, and streams the first `output_text` chunk back.  
- Future enhancement: parse file-search tool outputs plus `file_index.yaml` to return structured citations (wiki vs PDF).

### Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 4000
CMD ["uvicorn", "ask_api:app", "--host", "0.0.0.0", "--port", "4000"]
```

### Static UI

`/opt/bsos-wiki-data/ask/index.html` (already created) is a dark, NotebookLM-style chat pane that POSTs to `./api/ask`. Served by Caddy at `https://bsos.wiki/ask`.

---

## Multi-Wiki Pattern

To stand up another private wiki:

1. Clone the directory layout (`/opt/<name>-stack`, `/opt/<name>-data`, `/opt/<name>-indexer`).  
2. Add a new domain block in `Caddyfile`.  
3. Duplicate the Docker compose stack with unique container names, Git repo, OpenAI vector store ID, etc.  
4. Adjust prompts/configs to reference the new wiki’s domain.  

Everything stays isolated while reusing the same blueprint.

---

## Common Commands

```bash
# Bring up or down the stack
cd /opt/bsos-wiki-stack
docker-compose up -d
docker-compose down

# Inspect containers
docker ps

# Tail logs
docker logs -f bsos-wiki       # Wiki.js
docker logs -f bsos-ask        # Ask API

# Test Ask API (local)
curl -X POST http://localhost:4000/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"Explain the cardiac mechanism of irreversible hemorrhagic shock."}'

# Test Ask API via HTTPS
curl -X POST https://bsos.wiki/ask/api/ask \
  -H "Content-Type: application/json" \
  -d '{"query":"Explain the cardiac mechanism..."}'
```

---

This README now captures everything from bare-metal provisioning through Caddy, Docker, Wiki.js, Git sync, OpenAI indexing, and the Ask UI—without losing any detail from the original notes. Use it as the canonical SOP for BSOS or duplicate it for future wikis.***
