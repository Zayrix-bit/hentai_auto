# ⚡ Cloud Torrent/AnimeTosho to DropEmbed Automation

Zero-bandwidth, cloud-to-cloud automated pipeline that downloads content from torrents / AnimeTosho on GitHub Actions (Azure Datacenter) and streams it directly to [DropEmbed](https://dropembed.com/) via API.

> **💡 Zero Home Bandwidth:** Aapke home WiFi ka 1 MB bhi use nahi hota. Sab kuch cloud runner (1 Gbps speed) par process hota hai.

---

## 🛠️ Step-by-Step Setup Guide

### 1. Create a Private GitHub Repository
1. GitHub par jao aur ek naya repository create karo: [github.com/new](https://github.com/new)
2. **Repository name:** e.g. `dropembed-pipeline`
3. **Visibility:** **Private** select karo (Security & DMCA privacy ke liye).
4. Is workspace ke files ko apne GitHub repo me push kar do:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<YOUR_USERNAME>/<REPO_NAME>.git
   git push -u origin main
   ```

---

### 2. Add DropEmbed API Key in GitHub Secrets
1. Apne DropEmbed account se API Key copy karo: [dropembed.com/settings#api_settings](https://dropembed.com/settings#api_settings)
2. GitHub Repository me jao ➔ **Settings** tab par click karo.
3. Left sidebar me **Secrets and variables** ➔ **Actions** par click karo.
4. **New repository secret** button dabao:
   - **Name:** `DROPEMBED_API_KEY`
   - **Secret:** (Aapka DropEmbed API Key paste karo, e.g., `dpe_live_...`)
5. Click **Add secret**.

---

### 3. Enable Workflow Write Permissions (One-time setting)
GitHub Actions ko `videos.json` auto-save karne ki permission chahiye:
1. GitHub Repo ➔ **Settings** ➔ **Actions** ➔ **General** me jao.
2. Niche scroll karo **Workflow permissions** section par.
3. **"Read and write permissions"** select karo.
4. Click **Save**.

---

## 🚀 How to Run (Download & Upload to DropEmbed)

1. Apne GitHub repo ke **Actions** tab par click karo.
2. Left sidebar se **"Upload to DropEmbed (Cloud Runner)"** workflow select karo.
3. **"Run workflow"** button par click karo.
4. Form me details bharo:
   - **Source URL:** Magnet link, AnimeTosho link (`https://animetosho.org/view/...`), ya `.torrent` link paste karo.
   - **Custom Video Title (Optional):** Agar custom naam dena ho, varna auto-detect ho jayega.
   - **DropEmbed Folder ID (Optional):** Agar specific folder me daalna ho.
5. **Run workflow** dabao!

---

## 📊 Results & Embed Codes

Workflow complete hone ke baad:
1. **GitHub Job Summary:** Runner page par samne copyable `<iframe>` code aur links mil jayenge.
2. **`data/videos.json` & `data/videos.md`:** Repository me auto-update ho jayega jisme aapke saare uploaded videos ka record store rahega.
