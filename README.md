# 🔥 IL Pick 3 + Fireball Dashboard

Live Illinois Pick 3 + Fireball lottery analysis dashboard hosted on GitHub Pages.  
Automatically scrapes the Illinois Lottery site twice daily via GitHub Actions.

**Live URL:** https://bugula.github.io/il-lottery

---

## How it works

```
GitHub Actions (runs 2x/day)
  └── scripts/scrape.py  →  fetches illinoislotterynumbers.net
        └── saves data/draws.json  →  commits to repo
              └── deploys src/index.html  →  GitHub Pages
```

- No server, no database, no costs
- Data auto-updates after each drawing (midday + evening)
- First run auto-seeds all history back to 2010 (~11,000 draws)

---

## Setup (one time, ~10 minutes)

### Step 1 — Create the GitHub repo

1. Go to https://github.com/new
2. Name it exactly: `il-lottery`
3. Set visibility to **Public** (required for free GitHub Pages)
4. **Do not** initialize with a README
5. Click **Create repository**

### Step 2 — Push this code

```bash
cd il-lottery
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/bugula/il-lottery.git
git push -u origin main
```

### Step 3 — Enable GitHub Pages

1. In your repo → **Settings** → **Pages** (left sidebar)
2. Under **Build and deployment → Source**, select **GitHub Actions**
3. Save

### Step 4 — Trigger the first run

1. Go to your repo → **Actions** tab
2. Click **Scrape & Deploy** in the left sidebar
3. Click **Run workflow** → leave inputs as default → click **Run workflow**
4. The workflow detects `draws.json` is empty and **automatically seeds all data back to 2010**
5. Wait ~3–4 minutes for it to finish (scraping 16 years of data)

### Step 5 — View your dashboard

🎉 **https://bugula.github.io/il-lottery**

---

## Ongoing updates

The scraper runs automatically at:
- **1:00 PM CT** — after the midday drawing (~12:40 PM CT)  
- **9:30 PM CT** — after the evening drawing (~9:22 PM CT)

No action needed — data stays fresh automatically.

---

## Manual controls

**Refresh data now:**  
Actions → Scrape & Deploy → Run workflow (leave inputs default)

**Re-seed all history from 2010:**  
Actions → Scrape & Deploy → Run workflow → set `full_seed` to `yes`

---

## File structure

```
il-lottery/
├── .github/
│   └── workflows/
│       └── scrape-and-deploy.yml   # Scheduled GitHub Action (runs 2x daily)
├── scripts/
│   └── scrape.py                   # Python scraper (stdlib only, no pip needed)
├── src/
│   └── index.html                  # Full dashboard (single self-contained file)
├── data/
│   └── draws.json                  # Auto-updated by scraper, read by dashboard
└── README.md
```
