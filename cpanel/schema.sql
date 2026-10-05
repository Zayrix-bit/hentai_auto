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
  `poster_url` VARCHAR(500) NULL,
  `thumbnail_url` VARCHAR(500) NULL,
  `banner_url` VARCHAR(500) NULL,
  `description` TEXT NULL,
  `genres` VARCHAR(255) NULL,
  `year` INT NULL,
  `source_input` TEXT NULL,
  `views` INT DEFAULT 0,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Migration query if you already created the table earlier:
ALTER TABLE `videos` 
  ADD COLUMN IF NOT EXISTS `poster_url` VARCHAR(500) NULL,
  ADD COLUMN IF NOT EXISTS `thumbnail_url` VARCHAR(500) NULL,
  ADD COLUMN IF NOT EXISTS `banner_url` VARCHAR(500) NULL,
  ADD COLUMN IF NOT EXISTS `description` TEXT NULL,
  ADD COLUMN IF NOT EXISTS `genres` VARCHAR(255) NULL,
  ADD COLUMN IF NOT EXISTS `year` INT NULL;
