#!/usr/bin/env bash
#
# setup_wiki_interactive.sh
#
# Fully interactive bootstrap script for a fresh Ubuntu 24.04 (or 22.04) droplet
# when you have a SINGLE Git repo containing BOTH:
#   - the main stack files (docker-compose.yml, etc.)
#   - the indexer / Ask API code (Dockerfile, ask_api.py, indexer scripts)
#
# Repo layout assumption (you can customize subpaths):
#
#   <your-repo>/
#     stack/      # docker-compose.yml lives here
#     indexer/    # Dockerfile + ask_api.py + indexer live here
#
# The script will:
#   - Install Docker, Docker Compose v2, Caddy, UFW
#   - Create /opt/<wiki-name>-{stack,data,indexer}
#   - Clone your single repo into /opt/<wiki-name>-repo
#   - Copy the stack subdir into /opt/<wiki-name>-stack
#   - Copy the indexer subdir into /opt/<wiki-name>-indexer
#   - Write a Caddyfile for your domain
#   - Inject OPENAI_API_KEY into /opt/<wiki-name>-stack/.env
#   - Run docker-compose up -d
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must be run as root."
  exit 1
fi

echo "=== Interactive Wiki Stack Bootstrap (single repo) ==="

# --- Collect parameters interactively ---

read -rp "Wiki short name (e.g. bsos-wiki): " WIKI_NAME
if [[ -z "${WIKI_NAME}" ]]; then
  echo "Wiki name cannot be empty."
  exit 1
fi

read -rp "Domain name (e.g. bsos.wiki): " DOMAIN
if [[ -z "${DOMAIN}" ]]; then
  echo "Domain cannot be empty."
  exit 1
fi

read -rp "Email for Let's Encrypt notifications (e.g. you@example.com): " EMAIL
if [[ -z "${EMAIL}" ]]; then
  echo "Email cannot be empty."
  exit 1
fi

read -rp "Single Git repo URL (contains both stack and indexer): " MONO_REPO
if [[ -z "${MONO_REPO}" ]]; then
  echo "Repo URL cannot be empty."
  exit 1
fi

read -rp "Relative path to stack subdir within repo (default: stack): " STACK_SUBDIR
STACK_SUBDIR=${STACK_SUBDIR:-stack}

read -rp "Relative path to indexer subdir within repo (default: indexer): " INDEXER_SUBDIR
INDEXER_SUBDIR=${INDEXER_SUBDIR:-indexer}

read -rp "OpenAI API key (sk-...): " OPENAI_API_KEY
if [[ -z "${OPENAI_API_KEY}" ]]; then
  echo "OPENAI_API_KEY cannot be empty."
  exit 1
fi

STACK_DIR="/opt/${WIKI_NAME}-stack"
DATA_DIR="/opt/${WIKI_NAME}-data"
INDEXER_DIR="/opt/${WIKI_NAME}-indexer"
REPO_DIR="/opt/${WIKI_NAME}-repo"

echo ""
echo "Summary:"
echo "  WIKI_NAME      = ${WIKI_NAME}"
echo "  DOMAIN         = ${DOMAIN}"
echo "  EMAIL          = ${EMAIL}"
echo "  MONO_REPO      = ${MONO_REPO}"
echo "  STACK_SUBDIR   = ${STACK_SUBDIR}"
echo "  INDEXER_SUBDIR = ${INDEXER_SUBDIR}"
echo "  STACK_DIR      = ${STACK_DIR}"
echo "  DATA_DIR       = ${DATA_DIR}"
echo "  INDEXER_DIR    = ${INDEXER_DIR}"
echo "  REPO_DIR       = ${REPO_DIR}"
echo ""
read -rp "Proceed with these settings? [y/N]: " CONFIRM
CONFIRM=${CONFIRM:-n}
if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
  echo "Aborted by user."
  exit 1
fi

echo "=== [1/8] Updating system and installing base packages ==="
apt update && apt upgrade -y
apt install -y git curl ufw debian-keyring debian-archive-keyring apt-transport-https

echo "=== [2/8] Configure UFW (firewall) ==="
ufw allow OpenSSH || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
ufw --force enable

echo "=== [3/8] Install Docker ==="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | bash
  usermod -aG docker root
else
  echo "Docker already installed."
fi

echo "=== [4/8] Install Docker Compose v2 ==="
if ! command -v docker-compose >/dev/null 2>&1; then
  curl -L \
    "https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/bin/docker-compose
  chmod +x /usr/local/bin/docker-compose
else
  echo "docker-compose already installed."
fi

echo "=== [5/8] Install Caddy ==="
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' |
    gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' |
    tee /etc/apt/sources.list.d/caddy-stable.list
  apt update
  apt install -y caddy
else
  echo "Caddy already installed."
fi

echo "=== [6/8] Create directory layout ==="
mkdir -p "${STACK_DIR}"
mkdir -p "${DATA_DIR}"
mkdir -p "${DATA_DIR}/uploads"
mkdir -p "${DATA_DIR}/ask"
mkdir -p "${INDEXER_DIR}"
mkdir -p "${REPO_DIR}"

echo "Setting permissions on ${DATA_DIR}..."
chown -R 1000:1000 "${DATA_DIR}" || true
chmod 755 "${DATA_DIR}/uploads" "${DATA_DIR}/ask"

echo "=== [7/8] Configure Caddy for ${DOMAIN} ==="
cat > /etc/caddy/Caddyfile <<EOF
{
    email ${EMAIL}
}

${DOMAIN} {
    handle_path /ask/api/* {
        reverse_proxy localhost:4000
    }

    handle_path /ask/* {
        root * ${DATA_DIR}/ask
        file_server
    }

    handle_path /uploads/* {
        root * ${DATA_DIR}/uploads
        file_server
    }

    reverse_proxy localhost:3000
}
EOF

echo "Reloading Caddy..."
systemctl reload caddy || systemctl restart caddy

echo "=== Cloning mono-repo into ${REPO_DIR} ==="
if [[ -d "${REPO_DIR}/.git" ]]; then
  echo "WARNING: ${REPO_DIR} already contains a git repo; skipping clone."
else
  rm -rf "${REPO_DIR:?}"/*
  git clone "${MONO_REPO}" "${REPO_DIR}"
fi

if [[ ! -d "${REPO_DIR}/${STACK_SUBDIR}" ]]; then
  echo "ERROR: Stack subdir '${STACK_SUBDIR}' not found in ${REPO_DIR}"
  exit 1
fi

if [[ ! -d "${REPO_DIR}/${INDEXER_SUBDIR}" ]]; then
  echo "ERROR: Indexer subdir '${INDEXER_SUBDIR}' not found in ${REPO_DIR}"
  exit 1
fi

echo "=== Copying stack subdir into ${STACK_DIR} ==="
rm -rf "${STACK_DIR:?}"/*
cp -r "${REPO_DIR}/${STACK_SUBDIR}/." "${STACK_DIR}/"

echo "=== Copying indexer subdir into ${INDEXER_DIR} ==="
rm -rf "${INDEXER_DIR:?}"/*
cp -r "${REPO_DIR}/${INDEXER_SUBDIR}/." "${INDEXER_DIR}/"

echo "=== Injecting OPENAI_API_KEY into ${STACK_DIR}/.env ==="
ENV_FILE="${STACK_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  sed -i '/^OPENAI_API_KEY=/d' "${ENV_FILE}"
fi
echo "OPENAI_API_KEY=${OPENAI_API_KEY}" >> "${ENV_FILE}"

echo "=== Bringing up docker-compose stack ==="
cd "${STACK_DIR}"
docker-compose up -d --build

echo "=================================================================="
echo "Bootstrap complete for wiki '${WIKI_NAME}' at ${DOMAIN}."
echo ""
echo "Next steps:"
echo "  1) Visit: https://${DOMAIN}  → complete Wiki.js setup."
echo "  2) In Wiki.js, configure Git Storage to point at your content repo."
echo "  3) Populate ${DATA_DIR}/uploads with PDFs as needed."
echo "  4) Use the indexer in ${INDEXER_DIR} to build the OpenAI vector store."
echo ""
echo "You can inspect containers with:"
echo "  cd ${STACK_DIR}"
echo "  docker-compose ps"
echo "  docker-compose logs -f"
echo "=================================================================="
