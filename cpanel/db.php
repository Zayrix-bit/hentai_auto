<?php
/**
 * cPanel MySQL Database Connection
 * Update these credentials with your cPanel MySQL details!
 */

$db_host = 'localhost';              // Usually 'localhost' on cPanel
$db_name = 'jeevanka_hentai2_video'; // Aapka exact DB Name
$db_user = 'jeevanka_dbuser';        // Aapka exact DB User
$db_pass = 'Ritesh0909op@@@';        // Aapka database password

// API Secret Key to protect add_video.php from unauthorized requests
$api_secret = 'jeevankart_dpe_secret_2026';

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
