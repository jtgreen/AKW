#!/usr/bin/env python3
"""
setup_wiki_interactive.py

Fully interactive bootstrap for a fresh Ubuntu 22.04/24.04 droplet.

Assumes this repo is already cloned on the server, with structure:

  repo_root/
    stack/
      docker-compose.yml
      .env (will be used as base, not overwritten)
      Caddyfile (optional template; this script writes /etc/caddy/Caddyfile itself)
      ...
    indexer/
      Dockerfile
      ask_api.py
      indexer_stub.py
      ...
    scripts/
      setup_wiki_interactive.py

This script will:

  - Install Docker, Docker Compose v2, Caddy, UFW
  - Create /opt/<wiki-name>-{stack,data,indexer}
  - Copy stack/ → /opt/<wiki-name>-stack/
  - Copy indexer/ → /opt/<wiki-name>-indexer/
  - Create /opt/<wiki-name>-data/{uploads,ask}
  - Write /etc/caddy/Caddyfile for the given DOMAIN
  - Inject OPENAI_API_KEY into /opt/<wiki-name>-stack/.env
  - Run `docker-compose up -d --build` in /opt/<wiki-name>-stack

Run as root:

  python3 scripts/setup_wiki_interactive.py
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def run(cmd, check=True):
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, check=check)


def replace_tokens(path: Path, replacements: dict[str, str]) -> None:
    if not path.exists():
        return
    text = path.read_text()
    original = text
    for old, new in replacements.items():
        text = text.replace(old, new)
    if text != original:
        path.write_text(text)


def main():
    if os.geteuid() != 0:
        print("This script must be run as root (sudo).")
        sys.exit(1)

    print("=== Interactive Wiki Stack Bootstrap ===")

    wiki_name = input("Wiki short name (e.g. bsos-wiki): ").strip()
    if not wiki_name:
        print("Wiki name cannot be empty.")
        sys.exit(1)

    domain = input("Domain name (e.g. bsos.wiki): ").strip()
    if not domain:
        print("Domain cannot be empty.")
        sys.exit(1)

    email = input("Email for Let's Encrypt notifications (e.g. you@example.com): ").strip()
    if not email:
        print("Email cannot be empty.")
        sys.exit(1)

    openai_api_key = input("OpenAI API key (sk-...): ").strip()
    if not openai_api_key:
        print("OPENAI_API_KEY cannot be empty.")
        sys.exit(1)

    repo_url = input("Git repo URL (e.g. git@github.com:you/knowledge-wiki.git): ").strip()
    if not repo_url:
        print("Repo URL cannot be empty.")
        sys.exit(1)

    repo_dir_name = input("Directory name under /opt for this repo (default: wiki name): ").strip() or wiki_name
    repo_root = Path("/opt") / repo_dir_name
    stack_dir = repo_root / "stack"
    indexer_dir = repo_root / "indexer"
    data_dir = repo_root / "data"

    print("\nSummary:")
    print(f"  repo_url    = {repo_url}")
    print(f"  repo_root   = {repo_root}")
    print(f"  repo_root   = {repo_root}")
    print(f"  stack_dir   = {stack_dir}")
    print(f"  indexer_dir = {indexer_dir}")
    print(f"  data_dir    = {data_dir}")
    print(f"  stack_dir   = {stack_dir}")
    print(f"  indexer_dir = {indexer_dir}")
    print(f"  data_dir    = {data_dir}")
    print(f"  domain      = {domain}")
    print(f"  email       = {email}")
    print("")

    confirm = input("Proceed with these settings? [y/N]: ").strip().lower() or "n"
    if confirm != "y":
        print("Aborted by user.")
        sys.exit(1)

    # --- System prep ---

    print("\n=== [1/8] Updating system & installing base packages ===")
    run(["apt", "update"])
    run(["apt", "upgrade", "-y"])
    run(["apt", "install", "-y", "git", "curl", "ufw", "debian-keyring",
         "debian-archive-keyring", "apt-transport-https"])

    print("\n=== [2/8] Installing Python tooling (python3, pip, venv) ===")
    run(["apt", "install", "-y", "python3", "python3-pip", "python3-venv"])

    print("\n=== [3/8] Configure UFW ===")
    run(["ufw", "allow", "OpenSSH"], check=False)
    run(["ufw", "allow", "80/tcp"], check=False)
    run(["ufw", "allow", "443/tcp"], check=False)
    run(["ufw", "--force", "enable"])

    print("\n=== [4/8] Install Docker ===")
    if shutil.which("docker") is None:
        run(["bash", "-lc", "curl -fsSL https://get.docker.com | bash"])
        run(["usermod", "-aG", "docker", "root"], check=False)
    else:
        print("Docker already installed.")

    print("\n=== [5/8] Install Docker Compose v2 ===")
    if shutil.which("docker-compose") is None:
        compose_url = (
            f"https://github.com/docker/compose/releases/download/v2.27.0/"
            f"docker-compose-{os.uname().sysname}-{os.uname().machine}"
        )
        tmp_path = Path("/usr/local/bin/docker-compose")
        run(["curl", "-L", "-o", str(tmp_path), compose_url])
        os.chmod(tmp_path, 0o755)
    else:
        print("docker-compose already installed.")

    print("\n=== [6/9] Install Caddy ===")
    if shutil.which("caddy") is None:
        run([
            "curl", "-1sLf",
            "https://dl.cloudsmith.io/public/caddy/stable/gpg.key"
        ], check=False)
        # gpg may already be set up; try writing keyring
        run([
            "bash", "-lc",
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' "
            "| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
        ], check=False)
        run([
            "bash", "-lc",
            "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' "
            "| tee /etc/apt/sources.list.d/caddy-stable.list"
        ], check=False)
        run(["apt", "update"])
        run(["apt", "install", "-y", "caddy"])
    else:
        print("Caddy already installed.")

    print("\n=== [7/9] Fetching repository ===")
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    if repo_root.exists():
        if not (repo_root / ".git").is_dir():
            print(f"ERROR: {repo_root} exists but is not a git repository.")
            sys.exit(1)
        print(f"{repo_root} already exists; pulling latest changes...")
        run(["git", "-C", str(repo_root), "pull", "--ff-only"])
    else:
        run(["git", "clone", repo_url, str(repo_root)])

    if not stack_dir.is_dir():
        print(f"ERROR: Expected stack/ directory at {stack_dir}")
        sys.exit(1)
    if not indexer_dir.is_dir():
        print(f"ERROR: Expected indexer/ directory at {indexer_dir}")
        sys.exit(1)

    print("\n=== [8/9] Prepare data directories ===")
    data_dir.mkdir(parents=True, exist_ok=True)

    # data subdirs
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (data_dir / "ask").mkdir(parents=True, exist_ok=True)
    # relax: we'll let containers run as uid 1000, but root can still write
    try:
        run(["chown", "-R", "1000:1000", str(data_dir)], check=False)
    except Exception:
        pass
    os.chmod(data_dir / "uploads", 0o755)
    os.chmod(data_dir / "ask", 0o755)

    print("Setting up optional virtual environment for manual indexer runs...")
    venv_path = indexer_dir / ".venv"
    try:
        run(["python3", "-m", "venv", str(venv_path)])
        pip_bin = venv_path / "bin" / "pip"
        req_file = indexer_dir / "requirements.txt"
        if pip_bin.exists() and req_file.exists():
            run([str(pip_bin), "install", "-r", str(req_file)])
    except Exception as exc:
        print(f"Warning: unable to create virtualenv: {exc}")

    data_dir_str = str(data_dir)
    stack_dir_str = str(stack_dir)
    indexer_dir_str = str(indexer_dir)

    # Customize stack artifacts with user-provided values
    print("Customizing stack configuration files...")
    replace_tokens(
        stack_dir / "Caddyfile",
        {
            "bsos.wiki": domain,
            "john.travis.green@gmail.com": email,
            "/opt/knowledge-wiki/indexer/ask": f"{indexer_dir_str}/ask",
            "/opt/knowledge-wiki/data/uploads": f"{data_dir_str}/uploads",
        },
    )
    replace_tokens(
        stack_dir / "docker-compose.yml",
        {
            "bsos-wiki-db": f"{wiki_name}-db",
            "bsos-wiki-es": f"{wiki_name}-es",
            "bsos-wiki": wiki_name,
            "bsos-ask": f"{wiki_name}-ask",
            "/opt/knowledge-wiki/data": data_dir_str,
            "/opt/knowledge-wiki/indexer": indexer_dir_str,
        },
    )
    replace_tokens(
        stack_dir / "Makefile",
        {
            "/opt/knowledge-wiki/stack": stack_dir_str,
        },
    )

    print("\n=== [9/9] Configure Caddy for domain ===")
    caddyfile_path = Path("/etc/caddy/Caddyfile")
    caddyfile_text = f"""\
{{
    email {email}
}}

{domain} {{
    handle_path /ask/api/* {{
        reverse_proxy localhost:4000
    }}

    handle_path /ask/* {{
        root * {data_dir}/ask
        file_server
    }}

    handle_path /uploads/* {{
        root * {data_dir}/uploads
        file_server
    }}

    reverse_proxy localhost:3000
}}
"""
    caddyfile_path.write_text(caddyfile_text)
    print(f"Wrote Caddyfile to {caddyfile_path}")
    run(["systemctl", "reload", "caddy"], check=False)
    run(["systemctl", "restart", "caddy"], check=False)

    # Inject OPENAI_API_KEY into stack .env
    env_path = stack_dir / ".env"
    print(f"\nInjecting OPENAI_API_KEY into {env_path}")
    if not env_path.exists():
        env_path.write_text("")  # create empty .env if needed
    # Remove any existing OPENAI_API_KEY lines
    lines = env_path.read_text().splitlines()
    lines = [ln for ln in lines if not ln.strip().startswith("OPENAI_API_KEY=")]
    lines.append(f"OPENAI_API_KEY={openai_api_key}")
    env_path.write_text("\n".join(lines) + "\n")

    print("\n=== Starting docker-compose stack ===")
    os.chdir(stack_dir)
    run(["docker-compose", "up", "-d", "--build"])

    print("\n==================================================================")
    print(f"Bootstrap complete for wiki '{wiki_name}' at https://{domain}")
    print("")
    print("Next steps:")
    print(f"  1) Visit: https://{domain}  → complete Wiki.js setup (admin user, etc.).")
    print("  2) In Wiki.js, configure Git Storage to point at your wiki content repo.")
    print(f"  3) Place your Ask UI HTML in {data_dir}/ask/index.html (optional).")
    print(f"  4) Upload PDFs into {data_dir}/uploads and run your indexer to build the vector store.")
    print("")
    print(f"To inspect the stack:")
    print(f"  cd {stack_dir}")
    print("  docker-compose ps")
    print("  docker-compose logs -f")
    print("==================================================================")


if __name__ == "__main__":
    main()
