# 🌐 cPanel Database & Webhook Setup Guide

Yeh guide follow karke aap 2 minute me apne cPanel (`jeevankart.in`) par database setup kar sakte hain.

---

### Step 1: MySQL Database & User Create Karein
1. Apne cPanel dashboard me scroll down karke **Databases** section par jayein.
2. **"MySQL® Database Wizard"** par click karein:
   - **Step 1: Create A Database**
     - Database Name: `videos` (Full name banega: `jeevanka_videos`)
     - Click **Next Step**.
   - **Step 2: Create Database Users**
     - Username: `dbuser` (Full name banega: `jeevanka_dbuser`)
     - Password: *[Koi strong password set karein, e.g. `MySecurePass@123`]*
     - Click **Create User**.
   - **Step 3: Add User to the Database**
     - **"ALL PRIVILEGES"** checkbox ko tick karein.
     - Click **Make Changes**.

---

### Step 2: Database Table Create Karein (via phpMyAdmin)
1. cPanel home par wapas jayein aur **"phpMyAdmin"** par click karein.
2. Left side me apna database select karein: `jeevanka_videos`.
3. Top menu me **"SQL"** tab par click karein.
4. `cpanel/schema.sql` ka code copy karke paste karein aur **"Go"** dabayein:
   ```sql
   CREATE TABLE IF NOT EXISTS `videos` (
     `id` INT AUTO_INCREMENT PRIMARY KEY,
     `title` VARCHAR(255) NOT NULL,
     `video_id` VARCHAR(100) NOT NULL UNIQUE,
     `embed_url` VARCHAR(500) NOT NULL,
     `watch_url` VARCHAR(500) NOT NULL,
     `file_name` VARCHAR(255) NULL,
     `file_size_mb` DECIMAL(10,2) NULL,
     `source_input` TEXT NULL,
     `views` INT DEFAULT 0,
     `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
   ```
5. Table `videos` create ho jayegi!

---

### Step 3: Files ko cPanel File Manager me Upload Karein
1. cPanel me **"File Manager"** par click karein.
2. `public_html` folder open karein.
3. Ek naya folder banayein: `api`
4. Is `cpanel/` folder ki files ko upload karein:
   - `cpanel/db.php` ➔ `public_html/api/db.php`
   - `cpanel/add_video.php` ➔ `public_html/api/add_video.php`
   - `cpanel/get_videos.php` ➔ `public_html/api/get_videos.php`
   - `cpanel/player.html` ➔ `public_html/player.html`
5. `public_html/api/db.php` ko File Manager me right click karke **Edit** karein:
   - `$db_name = 'jeevanka_videos';`
   - `$db_user = 'jeevanka_dbuser';`
   - `$db_pass = 'AapkaPassword';`
   - `$api_secret = 'AapkaSecretToken';` (e.g. `jeevan_secret_key_99`)
   - Click **Save Changes**.

---

### Step 4: GitHub Secrets me 2 Variables Add Karein
Apne GitHub Repository (`Zayrix-bit/hentai_auto`) me jayein:
**Settings ➔ Secrets and variables ➔ Actions ➔ New repository secret**:

1. Secret 1:
   - **Name:** `CPANEL_API_URL`
   - **Secret:** `https://jeevankart.in/api/add_video.php`
2. Secret 2:
   - **Name:** `CPANEL_API_SECRET`
   - **Secret:** *[Wahi token jo aapne db.php me $api_secret me daala]*

---

### 🎉 Done! Kaise Test Karein?
Jab bhi GitHub Actions par video upload complete hoga:
1. Video DropEmbed par upload hogi.
2. Automatically `https://jeevankart.in/api/add_video.php` ko data bhejegi.
3. cPanel MySQL me record insert ho jayega.
4. Aap `https://jeevankart.in/player.html` open karke apni saari uploaded videos directly dekh sakte hain!
