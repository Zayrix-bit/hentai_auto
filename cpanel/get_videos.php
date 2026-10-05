<?php
/**
 * API Endpoint: Fetch Videos from cPanel MySQL Database
 * Returns JSON list of videos with pagination and search
 */

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *'); // Allow fetching from frontend
require_once __DIR__ . '/db.php';

$search = isset($_GET['search']) ? trim($_GET['search']) : '';
$limit  = isset($_GET['limit']) ? intval($_GET['limit']) : 50;
$offset = isset($_GET['offset']) ? intval($_GET['offset']) : 0;

try {
    if (!empty($search)) {
        $stmt = $pdo->prepare("SELECT * FROM `videos` WHERE `title` LIKE :search ORDER BY `id` DESC LIMIT :limit OFFSET :offset");
        $stmt->bindValue(':search', '%' . $search . '%', PDO::PARAM_STR);
    } else {
        $stmt = $pdo->prepare("SELECT * FROM `videos` ORDER BY `id` DESC LIMIT :limit OFFSET :offset");
    }
    
    $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    
    $videos = $stmt->fetchAll();

    // Total count
    $count_stmt = $pdo->query("SELECT COUNT(*) FROM `videos`");
    $total_count = $count_stmt->fetchColumn();

    echo json_encode([
        'success' => true,
        'total'   => intval($total_count),
        'count'   => count($videos),
        'videos'  => $videos
    ]);
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode([
        'success' => false,
        'error'   => 'Database error: ' . $e->getMessage()
    ]);
}
