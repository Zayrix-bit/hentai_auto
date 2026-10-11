<?php
/**
 * Streamtape Remote Upload & API Relay
 * 
 * This endpoint acts as an API relay for Streamtape API calls from cloud runners or scripts.
 *
 * Usage: POST /api/streamtape_relay.php
 * Body: { "action": "remotedl-add", "url": "https://...", "name": "...", "st_login": "...", "st_key": "..." }
 *   or: { "action": "remotedl-status", "id": "...", "st_login": "...", "st_key": "..." }
 *   or: { "action": "file-info", "file": "...", "st_login": "...", "st_key": "..." }
 *   or: { "action": "file-rename", "file": "...", "name": "...", "st_login": "...", "st_key": "..." }
 *   or: { "action": "list-folder", "folder": "...", "st_login": "...", "st_key": "..." }
 */

header('Content-Type: application/json; charset=utf-8');

// Optional: require authorization
if (file_exists(__DIR__ . '/db.php')) {
    require_once __DIR__ . '/db.php';
}
if (!empty($api_secret)) {
    $headers = getallheaders();
    $auth = $headers['Authorization'] ?? $headers['authorization'] ?? '';
    if ($auth !== 'Bearer ' . $api_secret) {
        http_response_code(401);
        echo json_encode(['success' => false, 'error' => 'Unauthorized']);
        exit;
    }
}

$input = json_decode(file_get_contents('php://input'), true);
if (!$input || empty($input['action']) || empty($input['st_login']) || empty($input['st_key'])) {
    http_response_code(400);
    echo json_encode(['success' => false, 'error' => 'action, st_login, and st_key required']);
    exit;
}

$action = $input['action'];
$login  = $input['st_login'];
$key    = $input['st_key'];

$base_url = 'https://api.streamtape.com';

switch ($action) {
    case 'remotedl-add':
        if (empty($input['url'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'url required']);
            exit;
        }
        $params = ['login' => $login, 'key' => $key, 'url' => $input['url']];
        if (!empty($input['name'])) {
            $params['name'] = $input['name'];
        }
        if (!empty($input['folder'])) {
            $params['folder'] = $input['folder'];
        }
        echo relay_get($base_url . '/remotedl/add?' . http_build_query($params));
        break;

    case 'remotedl-status':
        if (empty($input['id'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'id required']);
            exit;
        }
        $params = ['login' => $login, 'key' => $key, 'id' => $input['id']];
        echo relay_get($base_url . '/remotedl/status?' . http_build_query($params));
        break;

    case 'file-info':
        if (empty($input['file'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'file required']);
            exit;
        }
        $params = ['login' => $login, 'key' => $key, 'file' => $input['file']];
        echo relay_get($base_url . '/file/info?' . http_build_query($params));
        break;

    case 'file-rename':
        if (empty($input['file']) || empty($input['name'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'file and name required']);
            exit;
        }
        $params = ['login' => $login, 'key' => $key, 'file' => $input['file'], 'name' => $input['name']];
        echo relay_get($base_url . '/file/rename?' . http_build_query($params));
        break;

    case 'list-folder':
        $params = ['login' => $login, 'key' => $key];
        if (!empty($input['folder'])) {
            $params['folder'] = $input['folder'];
        }
        echo relay_get($base_url . '/file/listfolder?' . http_build_query($params));
        break;

    default:
        http_response_code(400);
        echo json_encode(['success' => false, 'error' => 'Unknown action: ' . $action]);
        break;
}

function relay_get(string $url): string {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_HTTPGET        => true,
        CURLOPT_HTTPHEADER     => [
            'Accept: application/json',
            'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 30,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_SSL_VERIFYPEER => true,
    ]);
    $response = curl_exec($ch);
    $http_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($error) {
        http_response_code(502);
        return json_encode(['success' => false, 'error' => 'Relay cURL error: ' . $error]);
    }
    http_response_code($http_code);
    return $response;
}
