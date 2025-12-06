# knowledge-wiki

Monorepo containing everything for the BSOS wiki:

```
knowledge-wiki/
  README.md           # this file (overview)
  stack/              # docker-compose + infra artifacts
  indexer/            # ingestion/indexing/Ask API code
  scripts/            # ops helpers (e.g., setup_wiki_interactive.sh)
```

- See `stack/MAIN_STACK_README.md` for server infrastructure notes.
- `indexer/README.md` covers the ingestion pipeline, Ask API, and the laptop-side `client_ingest/` scripts.
- `scripts/setup_wiki_interactive.sh` bootstraps a droplet using this layout.

## Ingest-Managed PDFs

Wiki.js aggressively purges unknown files from `/uploads`, so ingestion now targets a dedicated directory per wiki (`/opt/<wiki-name>-pdfs`) that Caddy serves at `https://<domain>/pdfs/...`. Point `--pdf-upload` (or the new `batch_ingest.py --pdf-upload-target`) at that path so PDFs survive purges and stay aligned with the Markdown summaries.

When you rebuild embeddings via `indexer/indexer_stub.py`, set `pdf_assets.local_dir` in `indexer/config.yaml` to that same directory (e.g., `/opt/bsos-wiki-pdfs`). The stub will automatically pull the uploaded `.txt` companions from there so the vector store contains both the Markdown summaries and the extracted PDF text.

## Prerequisites for Local Ingestion

If you plan to run the `client_ingest` scripts on your laptop, install the OCR dependencies before running `batch_ingest.py`:

```bash
# macOS (Homebrew)
brew install ghostscript tesseract

# Ubuntu/Debian
sudo apt install ghostscript tesseract-ocr
```

`ocrmypdf` requires `gs` (Ghostscript) on your PATH; without it, ingest runs will fail when falling back to OCR.


# Notes
Add to clean
Add the caddyfile and ask changes as the base landing page, refreeze

Add manual html to clean sidebar and searchbar
<script>
(function () {
  function updateHomeClass() {
    const path = window.location.pathname.replace(/\/+$/, '');

    // Treat these as "home":
    //  - "/"          → bare bsos.wiki
    //  - "/home"      → direct home route
    //  - "/en"        → localized root
    //  - "/en/home"   → localized home page
    const isHome =
      path === '' ||
      path === '/' ||
      path === '/home' ||
      path === '/en' ||
      path === '/en/home';

    document.documentElement.classList.toggle('bsos-home-clean', isHome);
  }

  // Run on initial load
  document.addEventListener('DOMContentLoaded', updateHomeClass);

  // Back/forward buttons
  window.addEventListener('popstate', updateHomeClass);

  // Hook SPA navigation
  (function () {
    const _pushState = history.pushState;
    history.pushState = function () {
      _pushState.apply(this, arguments);
      updateHomeClass();
    };

    const _replaceState = history.replaceState;
    history.replaceState = function () {
      _replaceState.apply(this, arguments);
      updateHomeClass();
    };
  })();

  // Safety net
  setInterval(updateHomeClass, 1000);
})();
</script>
<style>
  .bsos-home-clean .flex.page-col-sd.lg3.xl2 {
    display: none !important;
  }

  .bsos-home-clean .flex.page-col-content.xs12.lg9.xl10 {
    flex-basis: 100% !important;
    max-width: 100% !important;
  }
</style>
<script>
(function () {
  function nukeSearchBar() {
    // Kill the whole extension bar (what you pasted earlier)
    document.querySelectorAll('.v-toolbar__extension').forEach(el => {
      if (el && el.parentNode) {
        el.parentNode.removeChild(el);
      }
    });

    // Extra paranoia: remove any stray search inputs with that structure
    document.querySelectorAll('.v-input.v-text-field.v-text-field--solo').forEach(el => {
      if (el && el.parentNode) {
        el.parentNode.removeChild(el);
      }
    });
  }

  // Run once on load
  document.addEventListener('DOMContentLoaded', nukeSearchBar);

  // Run on SPA route changes (same trick as before)
  window.addEventListener('popstate', nukeSearchBar);

  (function () {
    const _pushState = history.pushState;
    history.pushState = function () {
      _pushState.apply(this, arguments);
      nukeSearchBar();
    };

    const _replaceState = history.replaceState;
    history.replaceState = function () {
      _replaceState.apply(this, arguments);
      nukeSearchBar();
    };
  })();

  // Failsafe: keep re-checking occasionally
  setInterval(nukeSearchBar, 1000);
})();
</script>

must change admin settings in wiki.js to allow iframe

update home for ask in permanent snapshot

?? What is the openai parser?