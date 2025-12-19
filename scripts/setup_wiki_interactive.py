#!/usr/bin/env python3
"""
setup_wiki_interactive.py

Fully interactive bootstrap for a fresh Ubuntu 22.04/24.04 droplet.

This script will clone/pull the monorepo into `/opt/<repo-dir>` and expects it to have
templates under `examples/`:

  repo_root/
    examples/
      stack/
        docker-compose.yml.example
        Makefile.example
        Caddyfile.example
        .env.example
      indexer/
        config.yaml.example
        .env.example
        ask/index.html.example
    indexer/               # indexer code (copied to /opt/<wiki-name>-indexer)
    scripts/setup_wiki_interactive.py

This script will:

  - Install Docker, Docker Compose v2, Caddy, UFW
  - Instantiate per-wiki directories:
      /opt/<wiki-name>-stack   (docker-compose.yml + Makefile + .env)
      /opt/<wiki-name>-indexer (Ask API + indexer code + config.yaml + .env)
      /opt/<wiki-name>-data    (Wiki.js data + served /uploads and /ask)
      /opt/<wiki-name>-pdfs    (served /pdfs)
  - Copy templates from `examples/` into the per-wiki directories above
  - Write /etc/caddy/Caddyfile for the given DOMAIN
  - Run `docker-compose up -d --build` in /opt/<wiki-name>-stack

Run as root:

  python3 scripts/setup_wiki_interactive.py
"""

import os
import re
import sys
import shutil
import subprocess
import secrets
from pathlib import Path


def run(cmd, check=True):
    print(f"+ {' '.join(cmd)}")
    subprocess.run(cmd, check=check)


def prompt_nonempty(label: str, example: str | None = None, default: str | None = None) -> str:
    suffix = ""
    if example:
        suffix = f" (e.g. {example})"
    if default:
        suffix += f" [default: {default}]"
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def require_slug(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit(f"{label} cannot be empty.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value):
        raise SystemExit(
            f"{label} must match [a-z0-9][a-z0-9-]* (max 63 chars). Got: {value!r}"
        )
    return value


def upsert_env_vars(env_path: Path, kv: dict[str, str]) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if env_path.exists():
        existing = env_path.read_text().splitlines()

    out: list[str] = []
    seen: set[str] = set()
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in kv:
            out.append(f"{key}={kv[key]}")
            seen.add(key)
        else:
            out.append(line)
            seen.add(key)

    for key, val in kv.items():
        if key not in seen:
            out.append(f"{key}={val}")

    env_path.write_text("\n".join(out).rstrip() + "\n")


def render_template_text(template: str, replacements: dict[str, str]) -> str:
    out = template
    for key, val in replacements.items():
        out = out.replace(f"{{{{{key}}}}}", val)
    return out


def render_template_file(src: Path, dst: Path, replacements: dict[str, str], *, overwrite: bool) -> None:
    if dst.exists() and not overwrite:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    dst.write_text(render_template_text(text, replacements), encoding="utf-8")


def copy_file(src: Path, dst: Path, *, overwrite: bool) -> None:
    if dst.exists() and not overwrite:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def main():
    if os.geteuid() != 0:
        print("This script must be run as root (sudo).")
        sys.exit(1)

    print("=== Interactive Wiki Stack Bootstrap ===")

    env_wiki_name = (os.getenv("WIKI_NAME") or "").strip() or None
    wiki_name = require_slug(
        prompt_nonempty("Wiki short name", example="bsos-wiki", default=env_wiki_name),
        "Wiki name",
    )

    domain = prompt_nonempty("Domain name", example="bsos.wiki", default=os.getenv("DOMAIN") or None)
    if not domain:
        raise SystemExit("Domain cannot be empty.")

    email = prompt_nonempty(
        "Email for Let's Encrypt notifications",
        example="you@example.com",
        default=os.getenv("EMAIL") or None,
    )
    if not email:
        raise SystemExit("Email cannot be empty.")

    openai_api_key = prompt_nonempty(
        "OpenAI API key",
        example="sk-...",
        default=os.getenv("OPENAI_API_KEY") or None,
    )
    if not openai_api_key:
        raise SystemExit("OPENAI_API_KEY cannot be empty.")

    repo_url = prompt_nonempty("Git repo URL", example="git@github.com:you/knowledge-wiki.git")
    if not repo_url:
        raise SystemExit("Repo URL cannot be empty.")

    repo_dir_name = prompt_nonempty(
        "Directory name under /opt for this monorepo clone",
        default="knowledge-wiki-remote",
    )
    repo_root = Path("/opt") / repo_dir_name

    wiki_stack_dir = Path("/opt") / f"{wiki_name}-stack"
    wiki_indexer_dir = Path("/opt") / f"{wiki_name}-indexer"
    data_dir = Path("/opt") / f"{wiki_name}-data"
    repo_dir = data_dir / "repo"
    pdf_dir = Path("/opt") / f"{wiki_name}-pdfs"

    wiki_base_url = f"https://{domain}"
    postgres_password = prompt_nonempty(
        "Postgres password for Wiki.js (leave blank to auto-generate)",
        default="",
    )
    if not postgres_password:
        postgres_password = secrets.token_urlsafe(24)

    overwrite = (input("Overwrite existing per-wiki files under /opt if present? [y/N]: ").strip().lower() == "y")

    print("\nSummary:")
    print(f"  repo_url    = {repo_url}")
    print(f"  repo_root   = {repo_root}")
    print(f"  stack_dir   = {wiki_stack_dir}")
    print(f"  indexer_dir = {wiki_indexer_dir}")
    print(f"  data_dir    = {data_dir}")
    print(f"  repo_dir    = {repo_dir}")
    print(f"  pdf_dir     = {pdf_dir}")
    print(f"  domain      = {domain}")
    print(f"  base_url    = {wiki_base_url}")
    print(f"  email       = {email}")
    print(f"  overwrite   = {overwrite}")
    print("")

    confirm = input("Proceed with these settings? [y/N]: ").strip().lower() or "n"
    if confirm != "y":
        print("Aborted by user.")
        sys.exit(1)

    # --- System prep ---

    print("\n=== [1/9] Updating system & installing base packages ===")
    run(["apt", "update"])
    run(["apt", "upgrade", "-y"])
    run(["apt", "install", "-y", "git", "curl", "ufw", "gnupg", "debian-keyring",
         "debian-archive-keyring", "apt-transport-https"])

    print("\n=== [2/9] Installing Python tooling (python3, pip, venv) ===")
    run(["apt", "install", "-y", "python3", "python3-pip", "python3-venv"])

    print("\n=== [3/9] Configure UFW ===")
    run(["ufw", "allow", "OpenSSH"], check=False)
    run(["ufw", "allow", "80/tcp"], check=False)
    run(["ufw", "allow", "443/tcp"], check=False)
    run(["ufw", "--force", "enable"])

    print("\n=== [4/9] Install Docker ===")
    if shutil.which("docker") is None:
        run(["bash", "-lc", "curl -fsSL https://get.docker.com | bash"])
        run(["usermod", "-aG", "docker", "root"], check=False)
    else:
        print("Docker already installed.")

    print("\n=== [5/9] Install Docker Compose v2 ===")
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

    repo_examples = repo_root / "examples"
    if not repo_examples.is_dir():
        raise SystemExit(f"ERROR: Expected examples/ directory at {repo_examples}")
    if not (repo_root / "indexer").is_dir():
        raise SystemExit(f"ERROR: Expected indexer/ directory at {repo_root / 'indexer'}")

    print("\n=== [8/9] Prepare data directories & config ===")
    data_dir.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (data_dir / "ask").mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    # relax: we'll let containers run as uid 1000, but root can still write
    try:
        run(["chown", "-R", "1000:1000", str(data_dir)], check=False)
    except Exception:
        pass
    try:
        run(["chown", "-R", "1000:1000", str(pdf_dir)], check=False)
    except Exception:
        pass
    os.chmod(data_dir / "uploads", 0o755)
    os.chmod(data_dir / "ask", 0o755)
    os.chmod(pdf_dir, 0o755)

    # Instantiate per-wiki stack + indexer from templates.
    templates_stack = repo_examples / "stack"
    templates_indexer = repo_examples / "indexer"
    if not templates_stack.is_dir():
        raise SystemExit(f"ERROR: Missing templates at {templates_stack}")
    if not templates_indexer.is_dir():
        raise SystemExit(f"ERROR: Missing templates at {templates_indexer}")

    wiki_stack_dir.mkdir(parents=True, exist_ok=True)
    wiki_indexer_dir.parent.mkdir(parents=True, exist_ok=True)

    print(f"Syncing indexer code → {wiki_indexer_dir}")
    shutil.copytree(repo_root / "indexer", wiki_indexer_dir, dirs_exist_ok=True)

    print(f"Instantiating stack templates → {wiki_stack_dir}")
    copy_file(
        templates_stack / "docker-compose.yml.example",
        wiki_stack_dir / "docker-compose.yml",
        overwrite=overwrite,
    )
    copy_file(
        templates_stack / "Makefile.example",
        wiki_stack_dir / "Makefile",
        overwrite=overwrite,
    )

    replacements = {
        "WIKI_NAME": wiki_name,
        "DOMAIN": domain,
        "EMAIL": email,
        "WIKI_BASE_URL": wiki_base_url,
    }

    # Optional: write a rendered Caddyfile template into the stack dir for reference.
    render_template_file(
        templates_stack / "Caddyfile.example",
        wiki_stack_dir / "Caddyfile",
        replacements,
        overwrite=overwrite,
    )

    print("Instantiating indexer templates...")
    render_template_file(
        templates_indexer / "config.yaml.example",
        wiki_indexer_dir / "config.yaml",
        {
            "WIKI_NAME": wiki_name,
            "WIKI_BASE_URL": wiki_base_url,
        },
        overwrite=overwrite,
    )
    render_template_file(
        templates_indexer / "ask" / "index.html.example",
        wiki_indexer_dir / "ask" / "index.html",
        {"WIKI_NAME": wiki_name},
        overwrite=overwrite,
    )

    # Seed Ask UI into /opt/<wiki>-data/ask unless the user already has one.
    ask_dst = data_dir / "ask" / "index.html"
    if not ask_dst.exists() or overwrite:
        render_template_file(
            templates_indexer / "ask" / "index.html.example",
            ask_dst,
            {"WIKI_NAME": wiki_name},
            overwrite=True,
        )

    print("Setting up optional virtual environment for manual indexer runs...")
    venv_path = wiki_indexer_dir / ".venv"
    try:
        run(["python3", "-m", "venv", str(venv_path)])
        pip_bin = venv_path / "bin" / "pip"
        req_file = wiki_indexer_dir / "requirements.txt"
        if pip_bin.exists() and req_file.exists():
            run([str(pip_bin), "install", "-r", str(req_file)])
    except Exception as exc:
        print(f"Warning: unable to create virtualenv: {exc}")

    # Write /opt/<wiki>-stack/.env (docker-compose variable substitutions)
    env_path = wiki_stack_dir / ".env"
    print(f"Writing stack environment file: {env_path}")
    upsert_env_vars(
        env_path,
        {
            "WIKI_NAME": wiki_name,
            "DOMAIN": domain,
            "WIKI_BASE_URL": wiki_base_url,
            "WIKI_DATA_DIR": str(data_dir),
            "WIKI_REPO_ROOT": str(repo_dir),
            "POSTGRES_PASSWORD": postgres_password,
            "OPENAI_API_KEY": openai_api_key,
            "WIKI_PDFS_DIR": str(pdf_dir),
            "INDEXER_DIR": str(wiki_indexer_dir),
        },
    )

    # Write /opt/<wiki>-indexer/.env for manual runs.
    upsert_env_vars(
        wiki_indexer_dir / ".env",
        {
            "OPENAI_API_KEY": openai_api_key,
            "WIKI_NAME": wiki_name,
            "WIKI_BASE_URL": wiki_base_url,
            "WIKI_REPO_ROOT": str(repo_dir),
            "WIKI_PDFS_DIR": str(pdf_dir),
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

    handle_path /pdfs/* {{
        root * {pdf_dir}
        file_server
    }}

    reverse_proxy localhost:3000
}}
"""
    caddyfile_path.write_text(caddyfile_text)
    print(f"Wrote Caddyfile to {caddyfile_path}")
    run(["systemctl", "reload", "caddy"], check=False)
    run(["systemctl", "restart", "caddy"], check=False)

    print("\n=== Starting docker-compose stack ===")
    os.chdir(wiki_stack_dir)
    run(["docker-compose", "up", "-d", "--build"])

    print("\n==================================================================")
    print(f"Bootstrap complete for wiki '{wiki_name}' at https://{domain}")
    print("")
    print("Next steps:")
    print(f"  1) Visit: https://{domain}  → complete Wiki.js setup (admin user, etc.).")
    print("  2) In Wiki.js, configure Git Storage to point at your wiki content repo.")
    print(f"  3) Place your Ask UI HTML in {data_dir}/ask/index.html (optional).")
    print(f"  4) Upload ingest-managed PDFs into {pdf_dir} (served at https://{domain}/pdfs/...) and run your indexer to build the vector store.")
    print("")
    print(f"To inspect the stack:")
    print(f"  cd {wiki_stack_dir}")
    print("  docker-compose ps")
    print("  docker-compose logs -f")
    print("==================================================================")


if __name__ == "__main__":
    main()
