<?php
/**
 * API Endpoint: Add / Sync Video to cPanel MySQL Database
 * Called automatically by GitHub Actions cloud pipeline
 */

header('Content-Type: application/json; charset=utf-8');
require_once __DIR__ . '/db.php';

// Verify Authorization Header if $api_secret is set
if (!empty($api_secret)) {
    $headers = getallheaders();
    $auth_header = isset($headers['Authorization']) ? $headers['Authorization'] : '';
    
    // Also check lower-case authorization
    if (empty($auth_header) && isset($headers['authorization'])) {
        $auth_header = $headers['authorization'];
    }

    $expected_auth = 'Bearer ' . $api_secret;
    if ($auth_header !== $expected_auth) {
        http_response_code(401);
        echo json_encode([
            'success' => false,
            'error' => 'Unauthorized: Invalid or missing API secret'
        ]);
        exit;
    }
}

// Read raw JSON POST input
$input_json = file_get_contents('php://input');
$data = json_decode($input_json, true);

if (!$data || empty($data['video_id']) || empty($data['title'])) {
    http_response_code(400);
    echo json_encode([
        'success' => false,
        'error' => 'Bad Request: video_id and title are required'
    ]);
    exit;
}

$title         = $data['title'];
$video_id      = $data['video_id'];
$embed_url     = isset($data['embed_url']) ? $data['embed_url'] : "https://dropembed.com/e/{$video_id}";
$watch_url     = isset($data['url']) ? $data['url'] : "https://dropembed.com/v/{$video_id}";
$file_name     = isset($data['file_name']) ? $data['file_name'] : null;
$file_size_mb  = isset($data['file_size_mb']) ? floatval($data['file_size_mb']) : null;
$source_input  = isset($data['source_input']) ? $data['source_input'] : null;
$poster_url    = isset($data['poster_url']) ? $data['poster_url'] : null;
$thumbnail_url = isset($data['thumbnail_url']) ? $data['thumbnail_url'] : null;
$banner_url    = isset($data['banner_url']) ? $data['banner_url'] : null;
$description   = isset($data['description']) ? $data['description'] : null;
$genres        = isset($data['genres']) ? $data['genres'] : null;
$year          = isset($data['year']) ? intval($data['year']) : null;

try {
    // Auto-migrate table if new columns are missing
    try {
        $pdo->exec("ALTER TABLE `videos` 
            ADD COLUMN IF NOT EXISTS `poster_url` VARCHAR(500) NULL,
            ADD COLUMN IF NOT EXISTS `thumbnail_url` VARCHAR(500) NULL,
            ADD COLUMN IF NOT EXISTS `banner_url` VARCHAR(500) NULL,
            ADD COLUMN IF NOT EXISTS `description` TEXT NULL,
            ADD COLUMN IF NOT EXISTS `genres` VARCHAR(255) NULL,
            ADD COLUMN IF NOT EXISTS `year` INT NULL;");
    } catch (Exception $e) {}

    // Insert or update on duplicate video_id
    $sql = "INSERT INTO `videos` 
            (`title`, `video_id`, `embed_url`, `watch_url`, `file_name`, `file_size_mb`, `source_input`, `poster_url`, `thumbnail_url`, `banner_url`, `description`, `genres`, `year`) 
            VALUES (:title, :video_id, :embed_url, :watch_url, :file_name, :file_size_mb, :source_input, :poster_url, :thumbnail_url, :banner_url, :description, :genres, :year)
            ON DUPLICATE KEY UPDATE 
            `title` = VALUES(`title`),
            `embed_url` = VALUES(`embed_url`),
            `watch_url` = VALUES(`watch_url`),
            `file_size_mb` = VALUES(`file_size_mb`),
            `poster_url` = VALUES(`poster_url`),
            `thumbnail_url` = VALUES(`thumbnail_url`),
            `banner_url` = VALUES(`banner_url`),
            `description` = VALUES(`description`),
            `genres` = VALUES(`genres`),
            `year` = VALUES(`year`);";

    $stmt = $pdo->prepare($sql);
    $stmt->execute([
        ':title'         => $title,
        ':video_id'      => $video_id,
        ':embed_url'     => $embed_url,
        ':watch_url'     => $watch_url,
        ':file_name'     => $file_name,
        ':file_size_mb'  => $file_size_mb,
        ':source_input'  => $source_input,
        ':poster_url'    => $poster_url,
        ':thumbnail_url' => $thumbnail_url,
        ':banner_url'    => $banner_url,
        ':description'   => $description,
        ':genres'        => $genres,
        ':year'          => $year,
    ]);

    echo json_encode([
        'success'   => true,
        'message'   => 'Video successfully saved to database',
        'video_id'  => $video_id,
        'title'     => $title
    ]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'error'   => 'Database error: ' . $e->getMessage()
    ]);
}
