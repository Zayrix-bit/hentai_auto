<?php
/**
 * cPanel MySQL Database Connection
 * Update these credentials with your cPanel MySQL details!
 */

$db_host = 'localhost';              // Usually 'localhost' on cPanel
$db_name = 'jeevanka_hentai2_video'; // Aapka exact DB Name
$db_user = 'jeevanka_dbuser';        // Aapka exact DB User
$db_pass = 'YOUR_DB_PASSWORD';       // Jo password aapne create kiya tha

// API Secret Key to protect add_video.php from unauthorized requests
// You can set any secure random string here, and set the same in GitHub Secrets (CPANEL_API_SECRET)
$api_secret = 'super_secret_token_123';

try {
    $pdo = new PDO("mysql:host={$db_host};dbname={$db_name};charset=utf8mb4", $db_user, $db_pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'error' => 'Database connection failed: ' . $e->getMessage()
    ]);
    exit;
}
