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

    # Repo structure: script is in repo_root/scripts/
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent
    stack_src = repo_root / "stack"
    indexer_src = repo_root / "indexer"

    if not stack_src.is_dir():
        print(f"ERROR: Expected stack/ directory at {stack_src}")
        sys.exit(1)
    if not indexer_src.is_dir():
        print(f"ERROR: Expected indexer/ directory at {indexer_src}")
        sys.exit(1)

    stack_dir = Path(f"/opt/{wiki_name}-stack")
    data_dir = Path(f"/opt/{wiki_name}-data")
    indexer_dir = Path(f"/opt/{wiki_name}-indexer")

    print("\nSummary:")
    print(f"  repo_root   = {repo_root}")
    print(f"  stack_src   = {stack_src}")
    print(f"  indexer_src = {indexer_src}")
    print(f"  stack_dir   = {stack_dir}")
    print(f"  data_dir    = {data_dir}")
    print(f"  indexer_dir = {indexer_dir}")
    print(f"  domain      = {domain}")
    print(f"  email       = {email}")
    print("")

    confirm = input("Proceed with these settings? [y/N]: ").strip().lower() or "n"
    if confirm != "y":
        print("Aborted by user.")
        sys.exit(1)

    # --- System prep ---

    print("\n=== [1/7] Updating system & installing base packages ===")
    run(["apt", "update"])
    run(["apt", "upgrade", "-y"])
    run(["apt", "install", "-y", "git", "curl", "ufw", "debian-keyring",
         "debian-archive-keyring", "apt-transport-https"])

    print("\n=== [2/7] Configure UFW ===")
    run(["ufw", "allow", "OpenSSH"], check=False)
    run(["ufw", "allow", "80/tcp"], check=False)
    run(["ufw", "allow", "443/tcp"], check=False)
    run(["ufw", "--force", "enable"])

    print("\n=== [3/7] Install Docker ===")
    if shutil.which("docker") is None:
        run(["bash", "-lc", "curl -fsSL https://get.docker.com | bash"])
        run(["usermod", "-aG", "docker", "root"], check=False)
    else:
        print("Docker already installed.")

    print("\n=== [4/7] Install Docker Compose v2 ===")
    if shutil.which("docker-compose") is None:
        run([
            "curl", "-L",
            f"https://github.com/docker/compose/releases/download/v2.27.0/"
            f"docker-compose-{os.uname().sysname}-{os.uname().machine}"
        ], check=False)
        # Move to /usr/local/bin if downloaded to current dir
        if Path(f"docker-compose-{os.uname().sysname}-{os.uname().machine}").exists():
            shutil.move(
                f"docker-compose-{os.uname().sysname}-{os.uname().machine}",
                "/usr/local/bin/docker-compose"
            )
            os.chmod("/usr/local/bin/docker-compose", 0o755)
    else:
        print("docker-compose already installed.")

    print("\n=== [5/7] Install Caddy ===")
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

    print("\n=== [6/7] Create directory layout & copy stack/indexer ===")
    for d in (stack_dir, data_dir, indexer_dir):
        d.mkdir(parents=True, exist_ok=True)

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

    # Copy stack and indexer contents (including Makefile, Caddyfile, etc.)
    print(f"Copying {stack_src} → {stack_dir}")
    # Clear dest and copy tree
    for item in stack_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in stack_src.iterdir():
        dest = stack_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print(f"Copying {indexer_src} → {indexer_dir}")
    for item in indexer_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in indexer_src.iterdir():
        dest = indexer_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    print("\n=== [7/7] Configure Caddy for domain ===")
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