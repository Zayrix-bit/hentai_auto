-- ========================================================
-- DropEmbed cPanel Database Schema
-- Run this in cPanel -> phpMyAdmin -> Your Database -> SQL
-- ========================================================

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
