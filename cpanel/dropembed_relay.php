<?php
/**
 * DropEmbed Remote Upload Relay
 * 
 * This endpoint acts as a Cloudflare bypass relay for DropEmbed API calls.
 * GitHub Actions runners (Azure datacenter IPs) get blocked by Cloudflare,
 * but this cPanel server (Hetzner VPS) has a clean IP that passes through.
 *
 * Usage: POST /api/dropembed_relay.php
 * Body: { "action": "remote-upload", "urls": ["..."], "dropembed_api_key": "dpe_..." }
 *   or: { "action": "update-title", "video_id": "...", "title": "...", "dropembed_api_key": "dpe_..." }
 *   or: { "action": "get-video", "video_id": "...", "dropembed_api_key": "dpe_..." }
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
if (!$input || empty($input['action']) || empty($input['dropembed_api_key'])) {
    http_response_code(400);
    echo json_encode(['success' => false, 'error' => 'action and dropembed_api_key required']);
    exit;
}

$action  = $input['action'];
$api_key = $input['dropembed_api_key'];

$base_headers = [
    'X-API-Key: ' . $api_key,
    'Content-Type: application/json',
    'Accept: application/json',
    'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
];

switch ($action) {
    case 'remote-upload':
        if (empty($input['urls'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'urls array required']);
            exit;
        }
        $result = relay_post(
            'https://dropembed.com/api/videos/remote-upload',
            json_encode(['urls' => $input['urls']]),
            $base_headers
        );
        echo $result;
        break;

    case 'update-title':
        if (empty($input['video_id']) || empty($input['title'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'video_id and title required']);
            exit;
        }
        $result = relay_request(
            'PATCH',
            'https://dropembed.com/api/videos/' . $input['video_id'],
            json_encode(['title' => $input['title']]),
            $base_headers
        );
        echo $result;
        break;

    case 'get-video':
        if (empty($input['video_id'])) {
            http_response_code(400);
            echo json_encode(['success' => false, 'error' => 'video_id required']);
            exit;
        }
        $result = relay_get(
            'https://dropembed.com/api/videos/' . $input['video_id'],
            $base_headers
        );
        echo $result;
        break;

    case 'list-videos':
        $result = relay_get(
            'https://dropembed.com/api/videos',
            $base_headers
        );
        echo $result;
        break;

    default:
        http_response_code(400);
        echo json_encode(['success' => false, 'error' => 'Unknown action: ' . $action]);
        break;
}

function relay_post(string $url, string $body, array $headers): string {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $body,
        CURLOPT_HTTPHEADER     => $headers,
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

function relay_get(string $url, array $headers): string {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_HTTPGET        => true,
        CURLOPT_HTTPHEADER     => $headers,
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

function relay_request(string $method, string $url, string $body, array $headers): string {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_POSTFIELDS     => $body,
        CURLOPT_HTTPHEADER     => $headers,
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
