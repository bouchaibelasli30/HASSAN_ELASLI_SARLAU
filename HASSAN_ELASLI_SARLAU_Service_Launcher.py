import urllib.request, urllib.error, base64, json, subprocess, sys, os
import types, tempfile, shutil, ssl

# ==========================================
GITHUB_USERNAME = "bouchaibelasli30"
GITHUB_REPO     = "Mercahova"
GITHUB_TOKEN    = "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"   # <- paste your token here
FOLDER          = "Mercahova/Service"
# ==========================================

MAIN_SCRIPT   = "AI_Automate_Marchepublics.py"
HELPER_SCRIPT = "pdf_price_extractor.py"
BINARY_FILES  = ["Name.png", "Valider.png"]
TENDERS_FILE  = "processed_tenders.txt"

LAUNCHER_DIR  = os.path.dirname(os.path.abspath(__file__))
TENDERS_PATH  = os.path.join(LAUNCHER_DIR, TENDERS_FILE)

# SSL FIX: works on all Windows laptops regardless of certificate store
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

if not os.path.exists(TENDERS_PATH):
    open(TENDERS_PATH, "w", encoding="utf-8").close()
    print(f"📄 Created: {TENDERS_FILE}")

# ─── GITHUB FETCH ────────────────────────────────────────────
def fetch_text(filename):
    print(f"📥 Fetching {filename}...")
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{FOLDER}/{filename}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Cache-Control": "no-cache"
    })
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as r:
            data = json.loads(r.read().decode())
            source = base64.b64decode(data["content"]).decode("utf-8")
        print(f"✅ {filename} ready.")
        return source
    except urllib.error.HTTPError as e:
        print(f"❌ Failed: {filename} → {e.code} {e.reason}")
        sys.exit(1)

def fetch_binary(filename):
    print(f"📥 Fetching {filename}...")
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{FOLDER}/{filename}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Cache-Control": "no-cache"
    })
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as r:
            data = json.loads(r.read().decode())
            content = base64.b64decode(data["content"])
        print(f"✅ {filename} ready.")
        return content
    except urllib.error.HTTPError as e:
        print(f"❌ Failed: {filename} → {e.code} {e.reason}")
        sys.exit(1)

# ─── IN-MEMORY RUNNER ────────────────────────────────────────
def run_in_memory():
    original_dir = os.getcwd()
    temp_dir = tempfile.mkdtemp()
    try:
        helper_source = fetch_text(HELPER_SCRIPT)
        helper_module = types.ModuleType("pdf_price_extractor")
        exec(compile(helper_source, HELPER_SCRIPT, "exec"), helper_module.__dict__)
        sys.modules["pdf_price_extractor"] = helper_module

        for img in BINARY_FILES:
            img_bytes = fetch_binary(img)
            with open(os.path.join(temp_dir, img), "wb") as f:
                f.write(img_bytes)

        os.chdir(temp_dir)

        main_source = fetch_text(MAIN_SCRIPT)
        patched_source = main_source.replace(
            'PROCESSED_FILE = "processed_tenders.txt"',
            f'PROCESSED_FILE = r"{TENDERS_PATH}"'
        )

        print("\n🚀 Launching automation...")
        main_globals = {"__name__": "__main__", "__file__": MAIN_SCRIPT}
        exec(compile(patched_source, MAIN_SCRIPT, "exec"), main_globals)

    finally:
        os.chdir(original_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)
        sys.modules.pop("pdf_price_extractor", None)
        print("🧹 Done. No files left behind.")

# ─── TENDER URL MANAGER ──────────────────────────────────────
# Run with:  python launcher.py manage
def load_urls():
    if not os.path.exists(TENDERS_PATH):
        return []
    with open(TENDERS_PATH, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def save_urls(urls):
    with open(TENDERS_PATH, "w", encoding="utf-8") as f:
        for url in urls:
            f.write(url + "\n")

def manage_tenders():
    while True:
        urls = load_urls()
        print("\n" + "=" * 60)
        print("          TENDER URL MANAGER")
        print("=" * 60)
        print(f"  Total processed tenders: {len(urls)}")
        print("-" * 60)
        print("  1 - View all URLs")
        print("  2 - Delete a URL  (script will re-process that tender)")
        print("  3 - Add a URL     (script will skip that tender)")
        print("  4 - Clear ALL URLs (script re-processes everything)")
        print("  5 - Exit manager")
        print("-" * 60)
        choice = input("  Choose: ").strip()

        if choice == "1":
            if not urls:
                print("\n  No URLs recorded yet.")
            else:
                print(f"\n  {'#':<5} URL")
                print("  " + "-" * 56)
                for i, url in enumerate(urls, 1):
                    display = url if len(url) <= 70 else "..." + url[-67:]
                    print(f"  {i:<5} {display}")

        elif choice == "2":
            if not urls:
                print("\n  No URLs to delete.")
                continue
            print(f"\n  {'#':<5} URL")
            print("  " + "-" * 56)
            for i, url in enumerate(urls, 1):
                display = url if len(url) <= 70 else "..." + url[-67:]
                print(f"  {i:<5} {display}")
            print()
            num = input("  Enter number to delete (or 0 to cancel): ").strip()
            if num == "0":
                continue
            try:
                idx = int(num) - 1
                if 0 <= idx < len(urls):
                    removed = urls.pop(idx)
                    save_urls(urls)
                    print(f"\n  Deleted. Script will re-process this tender next run:")
                    print(f"     {removed}")
                else:
                    print("  Invalid number.")
            except ValueError:
                print("  Please enter a number.")

        elif choice == "3":
            print("\n  Paste the full tender URL to mark as already processed:")
            new_url = input("  URL: ").strip()
            if new_url:
                if new_url in urls:
                    print("  This URL is already in the list.")
                else:
                    urls.append(new_url)
                    save_urls(urls)
                    print("  Added. Script will skip this tender.")
            else:
                print("  Empty URL - nothing added.")

        elif choice == "4":
            confirm = input("\n  Type YES to confirm clearing all URLs: ").strip()
            if confirm == "YES":
                save_urls([])
                print("  Cleared. Script will re-process everything on next run.")
            else:
                print("  Cancelled.")

        elif choice == "5":
            break
        else:
            print("  Invalid choice.")

# ─── ENTRY POINT ─────────────────────────────────────────────
if __name__ == "__main__":
    # Normal run:         python launcher.py
    # Manage URLs:        python launcher.py manage
    if len(sys.argv) > 1 and sys.argv[1] == "manage":
        manage_tenders()
    else:
        run_in_memory()