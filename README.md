# ⚡ HentaiDB to Streamtape Cloud Automation

Automated cloud-to-cloud pipeline that scrapes pure 2D anime series from [HentaiDB](https://hentaidb.xyz/), extracts direct Cloudflare CDN MP4 streams, fetches official MyAnimeList metadata, and uploads directly to [Streamtape](https://streamtape.com/) with cPanel MySQL database synchronization.

> **💡 Zero Home Bandwidth:** Aapke home WiFi ka 1 MB bhi use nahi hota. Sab kuch cloud runner (1 Gbps speed) par process hota hai.

---

## 🎯 Main Source: [HentaiDB.xyz](https://hentaidb.xyz/)

- **Master Directory:** 2,080 pure 2D anime series (`https://hentaidb.xyz/all`)
- **Direct Streams:** Cloudflare CDN high-speed MP4 (1080p / 720p H.264)
- **Zero 3D/CGI:** Strict automatic blacklist filter that blocks any 3D/SFM/Blender/Koikatsu content to guarantee 100% pure 2D anime.
- **Smart Deduplication:** Existing catalog in `data/videos.json` and Streamtape account are checked before every upload to prevent duplicates.

---

## 🛠️ Step-by-Step Setup Guide

### 1. Add Streamtape Credentials in GitHub Secrets
1. Apne Streamtape account se API credentials copy karein: [streamtape.com/accinfo](https://streamtape.com/accinfo)
   - **API Login**
   - **API Key**
2. GitHub Repository me jayein ➔ **Settings** ➔ **Secrets and variables** ➔ **Actions**.
3. **New repository secret** button dabayein:
   - **Name:** `STREAMTAPE_LOGIN`
   - **Secret:** (Aapka Streamtape API Login)
4. Aur ek secret add karein:
   - **Name:** `STREAMTAPE_KEY`
   - **Secret:** (Aapka Streamtape API Key)
5. Optional (cPanel Database sync ke liye):
   - `CPANEL_API_URL` (e.g. `https://jeevankart.in/api/add_video.php`)
   - `CPANEL_API_SECRET` (Aapka cPanel API Secret token)

---

### 2. Enable Workflow Write Permissions (One-time setting)
GitHub Actions ko `videos.json` auto-save karne ke liye:
1. GitHub Repo ➔ **Settings** ➔ **Actions** ➔ **General** me jayein.
2. Niche scroll karein **Workflow permissions** section par.
3. **"Read and write permissions"** select karein.
4. Click **Save**.

---

## 🚀 Workflows Available

### 1. Primary Automated Pipeline (`hentaidb_pipeline.yml`)
- **Automated Hourly Cron:** Har ghante automatic run hota hai (`30 * * * *`).
- **Manual Trigger:** Actions tab me jaake **"HentaiDB 2D Anime Pipeline"** choose karke **Run workflow** dabayein:
  - `videos_count`: Number of episodes to crawl (default: `50`)
  - `start_series_index`: Start series in directory `0-2080` (default: `0`)
  - `max_videos_per_worker`: Episodes per parallel worker (default: `10`)
- **10 Parallel Matrix Workers:** 10 cloud runners ek sath parallel me download & upload karte hain.

### 2. Live Sync Workflow (`sync_streamtape.yml`)
- Streamtape API `/file/listfolder` call karke account ke saare live videos ko fetch karta hai aur local `data/videos.json` aur cPanel MySQL database me sync karta hai.

### 3. Manual Single Video Uploader (`upload.yml`)
- Kisi bhi single HentaiDB watch URL, direct MP4 link, ya magnet URL ko manually upload karne ke liye.

---

## 📊 Results & Watching

1. **GitHub Job Summary:** Runner page par copyable `<iframe>` embed code aur links milte hain (`https://streamtape.com/e/{id}` & `https://streamtape.com/v/{id}`).
2. **`data/videos.json` & `data/videos.md`:** Repository me automatically save hota hai.
3. **cPanel Web Player:** `https://jeevankart.in/player.html` par directly responsive player me videos watch kar sakte hain!
