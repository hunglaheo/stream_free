import re
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from flask import Blueprint, Response, jsonify, render_template, request

try:
    from platforms.common import rewrite_m3u8_line
except ModuleNotFoundError:
    # Allows running this module directly from the platforms folder.
    from common import rewrite_m3u8_line

stripchat_bp = Blueprint("stripchat", __name__, url_prefix="/api/stripchat")

_stripchat_config = {
    "base_url": "https://vi.stripchat.com/api/front/v2/models",
    "related_url": "https://vi.stripchat.com/api/front/models",
    "site_origin": "https://vi.stripchat.com",
    "referer": "https://vi.stripchat.com/",
    "front_version": "11.8.28",
    "cookie": "",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "enabled": True,
}

_STRIPCHAT_CONFIG_KEYS = {
    "base_url",
    "related_url",
    "site_origin",
    "referer",
    "front_version",
    "cookie",
    "user_agent",
    "enabled",
}

_AD_MARKERS = (
    "#EXT-X-MOUFLON-ADVERT",
    "#EXT-X-PLAYLIST-TYPE:VOD",
    "/CPA/",
    "CHUNK_000.M4S",
)

_STREAM_INFO_BUDGET_SEC = 6.0
_HTTP_TIMEOUT_FAST = 4


def _build_stripchat_headers() -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "vi,en-US;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "front-version": _stripchat_config["front_version"],
        "pragma": "no-cache",
        "referer": _stripchat_config["referer"],
        "user-agent": _stripchat_config["user_agent"],
    }
    if _stripchat_config["cookie"]:
        headers["cookie"] = _stripchat_config["cookie"]
    return headers


def _build_media_headers() -> dict:
    headers = {
        "accept": "*/*",
        "accept-language": "vi,en-US;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "dnt": "1",
        "origin": _stripchat_config["site_origin"],
        "pragma": "no-cache",
        "referer": _stripchat_config["referer"],
        "user-agent": _stripchat_config["user_agent"],
    }
    if _stripchat_config["cookie"]:
        headers["cookie"] = _stripchat_config["cookie"]
    return headers


def _to_absolute_media_url(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return urljoin(_stripchat_config["site_origin"], path_or_url)


def _to_bool_arg(name: str, default: bool) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_hls_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        parts = urlsplit(raw_url)
        items = parse_qsl(parts.query, keep_blank_values=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(items), parts.fragment))
    except Exception:
        return raw_url


def _to_playback_hls_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        parts = urlsplit(raw_url)
        items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered in {"_hls_msn", "_hls_part"}:
                continue
            items.append((key, value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(items), parts.fragment))
    except Exception:
        return raw_url


def _scan_for_stream_urls(payload, collector: list) -> None:
    if payload is None:
        return

    if isinstance(payload, str):
        value = payload.strip().replace("\\/", "/").replace("\\u0026", "&")
        if value.startswith(("http://", "https://")) and ".m3u8" in value.lower() and value not in collector:
            collector.append(_normalize_hls_url(value))
        return

    if isinstance(payload, list):
        for item in payload:
            _scan_for_stream_urls(item, collector)
        return

    if isinstance(payload, dict):
        for value in payload.values():
            _scan_for_stream_urls(value, collector)


def _extract_m3u8_urls_from_html(html_text: str) -> list:
    if not html_text:
        return []

    patterns = [
        r"https://media-hls\\.doppiocdn\\.(?:org|com)[^\"'\\s]+\\.m3u8[^\"'\\s]*",
        r"https://edge-hls\\.doppiocdn\\.(?:org|com)[^\"'\\s]+\\.m3u8[^\"'\\s]*",
        r"https:\\/\\/media-hls\\.doppiocdn\\.(?:org|com)[^\"'\\s]+\\.m3u8[^\"'\\s]*",
        r"https:\\/\\/edge-hls\\.doppiocdn\\.(?:org|com)[^\"'\\s]+\\.m3u8[^\"'\\s]*",
    ]

    found = []
    for pattern in patterns:
        for match in re.findall(pattern, html_text):
            cleaned = match.replace("\\/", "/").replace("\\u0026", "&")
            normalized = _normalize_hls_url(cleaned)
            if normalized and normalized not in found:
                found.append(normalized)
    return found


def _extract_pkeys_from_html(html_text: str) -> list:
    if not html_text:
        return []

    patterns = [
        r"[?&]pkey=([A-Za-z0-9_-]+)",
        r'"pkey"\\s*:\\s*"([A-Za-z0-9_-]+)"',
        r"pkey\\\\u003d([A-Za-z0-9_-]+)",
        r"#EXT-X-MOUFLON:PSCH:v2:([A-Za-z0-9_-]+)",
    ]

    found = []
    for pattern in patterns:
        for item in re.findall(pattern, html_text):
            if item and item not in found:
                found.append(item)
    return found


def _extract_stream_id_from_hls_url(raw_url: str) -> str:
    if not raw_url:
        return ""

    patterns = [
        r"/hls/(\\d+)/",
        r"/b-hls-\\d+/(\\d+)/",
        r"/(\\d+)_\\d+p(?:_[a-z0-9_]+)?\\.m3u8",
        r"/(\\d+)\\.m3u8",
    ]

    for pattern in patterns:
        match = re.search(pattern, raw_url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_stream_id_from_html(html_text: str, username: str) -> str:
    if not html_text:
        return ""

    if username:
        escaped_username = re.escape(username.lower())
        pattern = (
            r'"userIdByUsername"\s*:\s*\{[^}]*"'
            + escaped_username
            + r'"\s*:\s*\{"type":"found","id":(\d+)'
        )
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    fallback = re.search(r'"id"\\s*:\\s*(\\d{6,})', html_text)
    if fallback:
        return fallback.group(1)

    return ""


def _extract_hls_base_host_from_url(raw_url: str) -> str:
    try:
        parts = urlsplit(raw_url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        pass
    return "https://media-hls.doppiocdn.org"


def _filter_candidates_by_stream_id(candidates: list, stream_id: str) -> list:
    if not stream_id:
        return list(candidates)

    filtered = []
    marker = f"/{stream_id}/"
    prefix = f"/{stream_id}_"

    for item in candidates:
        lowered = (item or "").lower()
        if marker in lowered or prefix in lowered or lowered.endswith(f"/{stream_id}.m3u8"):
            filtered.append(item)

    return filtered if filtered else list(candidates)


def _hls_candidate_score(url: str) -> int:
    lowered = (url or "").lower()
    score = 0
    if "pkey=" in lowered:
        score += 8
    if "playlisttype=lowlatency" in lowered:
        score += 6
    if "psch=v2" in lowered:
        score += 4
    if "media-hls.doppiocdn" in lowered:
        score += 8
    if "edge-hls.doppiocdn" in lowered:
        score += 4
    if "/master/" in lowered:
        score -= 4
    if "_hls_msn=" in lowered or "_hls_part=" in lowered:
        score -= 2
    return score


def _pick_best_hls_candidate(candidates: list) -> str:
    valid = [item for item in candidates if isinstance(item, str) and item.startswith(("http://", "https://"))]
    if not valid:
        return ""
    ranked = sorted(valid, key=_hls_candidate_score, reverse=True)
    return ranked[0]


def _fetch_stripchat_initial_config() -> dict:
    url = "https://ext.stripchat.com/api/v1/config/initial"
    headers = _build_media_headers()
    headers["sec-fetch-site"] = "same-site"
    try:
        resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT_FAST)
        data = resp.json() if "json" in (resp.headers.get("Content-Type", "").lower()) else {}
        return {
            "statusCode": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "data": data if isinstance(data, dict) else {},
        }
    except Exception:
        return {"statusCode": 500, "ok": False, "data": {}}


def _fetch_stripchat_stream_extensions(stream_id: str) -> dict:
    url = f"https://ext.stripchat.com/api/v1/stream/{stream_id}/extensions"
    headers = _build_media_headers()
    headers["sec-fetch-site"] = "same-site"
    try:
        resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT_FAST)
        data = resp.json() if "json" in (resp.headers.get("Content-Type", "").lower()) else {}
        return {
            "statusCode": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "data": data if isinstance(data, dict) else {},
        }
    except Exception:
        return {"statusCode": 500, "ok": False, "data": {}}


def _fetch_related_hls_candidates(stream_id: str, username: str) -> list:
    if not _stripchat_config.get("related_url"):
        return []

    request_variants = []
    if stream_id:
        base = _stripchat_config["related_url"].rstrip("/")
        request_variants.append((f"{base}/{stream_id}", {}))
        request_variants.append((f"{base}/{stream_id}/related", {}))
        request_variants.append((_stripchat_config["related_url"], {"id": stream_id}))
        request_variants.append((_stripchat_config["related_url"], {"modelId": stream_id}))
    if username:
        base = _stripchat_config["related_url"].rstrip("/")
        request_variants.append((f"{base}/{username}", {}))
        request_variants.append((f"{base}/{username}/related", {}))
        request_variants.append((_stripchat_config["related_url"], {"username": username}))

    headers = _build_stripchat_headers()
    headers["accept"] = "application/json, text/plain, */*"

    candidates = []
    seen = set()
    for url, params in request_variants:
        key = (url, tuple(sorted(params.items())))
        if key in seen:
            continue
        seen.add(key)

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=_HTTP_TIMEOUT_FAST)
            if not (200 <= resp.status_code < 300):
                continue
            payload = resp.json() if "json" in (resp.headers.get("Content-Type", "").lower()) else {}
            found = []
            _scan_for_stream_urls(payload, found)
            for item in found:
                if item not in candidates:
                    candidates.append(item)
        except Exception:
            continue

    return candidates


def _extract_nested_hls_candidates(playlist_url: str) -> tuple[list, list]:
    if not playlist_url or ".m3u8" not in playlist_url.lower():
        return [], []

    try:
        resp = requests.get(playlist_url, headers=_build_media_headers(), timeout=_HTTP_TIMEOUT_FAST)
    except Exception:
        return [], []

    content_type = (resp.headers.get("Content-Type", "") or "").lower()
    if "mpegurl" not in content_type and ".m3u8" not in (resp.url or "").lower():
        return [], []

    nested = []
    tokens = []

    for line in resp.text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#"):
            token_match = re.search(r"#EXT-X-MOUFLON:PSCH:v2:([A-Za-z0-9_-]+)", stripped)
            if token_match:
                token = token_match.group(1)
                if token and token not in tokens:
                    tokens.append(token)
            continue

        if ".m3u8" in stripped.lower():
            absolute = _normalize_hls_url(urljoin(resp.url, stripped))
            if absolute and absolute not in nested:
                nested.append(absolute)

    if tokens:
        base_variants = list(nested)
        if ".m3u8" in (resp.url or "").lower():
            base_variants.append(_normalize_hls_url(resp.url))

        for base_url in base_variants:
            try:
                parts = urlsplit(base_url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query["playlistType"] = query.get("playlistType") or "lowLatency"
                query["psch"] = "v2"
                for token in tokens:
                    query["pkey"] = token
                    enriched = urlunsplit(
                        (
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode(list(query.items())),
                            parts.fragment,
                        )
                    )
                    normalized = _normalize_hls_url(enriched)
                    if normalized and normalized not in nested:
                        nested.append(normalized)
            except Exception:
                continue

    return nested, tokens


def _expand_hls_candidates(seed_urls: list, max_depth: int = 2) -> tuple[list, list]:
    queue = [item for item in seed_urls if isinstance(item, str) and ".m3u8" in item.lower()][:4]
    seen = set(queue)
    expanded = []
    token_pool = []

    depth = 0
    while queue and depth < max_depth and len(expanded) < 24:
        current = list(queue)
        queue = []
        for url in current:
            nested, tokens = _extract_nested_hls_candidates(url)
            for token in tokens:
                if token not in token_pool:
                    token_pool.append(token)
            for item in nested:
                if item not in seen:
                    seen.add(item)
                    expanded.append(item)
                    if ".m3u8" in item.lower():
                        queue.append(item)
        depth += 1

    return expanded, token_pool


def _build_canonical_pkey_candidates(seed_urls: list, stream_id: str, pkeys: list) -> list:
    if not stream_id or not pkeys:
        return []

    seeds = [item for item in seed_urls if isinstance(item, str) and ".m3u8" in item.lower()]
    if not seeds:
        seeds = [f"https://media-hls.doppiocdn.org/b-hls-22/{stream_id}/{stream_id}_240p.m3u8"]

    candidates = []
    for seed in seeds:
        try:
            parts = urlsplit(seed)
            host = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "https://media-hls.doppiocdn.org"
            path = parts.path or ""

            cluster_match = re.search(r"/b-hls-(\\d+)/", path)
            cluster = cluster_match.group(1) if cluster_match else "22"
            canonical_path = f"/b-hls-{cluster}/{stream_id}/{stream_id}_240p.m3u8"

            for pkey in pkeys:
                query = {
                    "playlistType": "lowLatency",
                    "psch": "v2",
                    "pkey": pkey,
                }
                candidate = _normalize_hls_url(f"{host}{canonical_path}?{urlencode(list(query.items()))}")
                if candidate not in candidates:
                    candidates.append(candidate)
        except Exception:
            continue

    return candidates


def _is_ad_playlist(url: str) -> bool:
    if not url or ".m3u8" not in url.lower():
        return False

    try:
        resp = requests.get(url, headers=_build_media_headers(), timeout=3)
    except Exception:
        return False

    text = (resp.text or "").upper()
    return any(marker in text for marker in _AD_MARKERS)


def _build_room_config_output(
    username: str,
    stream_id: str,
    primary_stream: str,
    pkey_candidates: list,
    initial_config_meta: dict,
) -> dict:
    backup = ""
    for item in pkey_candidates:
        if "_240p" in (item or ""):
            backup = item
            break

    websocket = (initial_config_meta or {}).get("websocket", {}) if isinstance(initial_config_meta, dict) else {}

    return {
        "idol_name": username,
        "room_id": stream_id,
        "platform": "stripchat",
        "primary_stream": primary_stream,
        "backup_stream_240p": backup,
        "websocket_sync": {
            "address": websocket.get("url", ""),
            "auth_token": websocket.get("token", ""),
        },
    }


def _build_stripchat_avatar_url(model: dict) -> str:
    model_id = model.get("id")
    snapshot_ts = (
        model.get("verifiedSnapshotTimestamp")
        or model.get("snapshotTimestamp")
        or model.get("popularSnapshotTimestamp")
    )
    if model_id and snapshot_ts:
        return f"https://img.doppiocdn.org/snapshot/{model_id}/{snapshot_ts}"
    return _to_absolute_media_url(model.get("avatarUrl", ""))


def _to_stripchat_proxy_url(raw_url: str) -> str:
    return f"/api/stripchat/video_proxy?url={quote(raw_url, safe='')}"


def _build_upstream_url_variants(raw_url: str) -> list:
    """Build equivalent URL variants to reduce transient CDN 404 failures."""
    normalized = _normalize_hls_url(raw_url)
    variants = []

    for candidate in [raw_url, normalized]:
        if candidate and candidate not in variants:
            variants.append(candidate)

    for candidate in list(variants):
        if "doppiocdn.com" in candidate:
            swapped = candidate.replace("doppiocdn.com", "doppiocdn.org")
            if swapped not in variants:
                variants.append(swapped)
        if "doppiocdn.org" in candidate:
            swapped = candidate.replace("doppiocdn.org", "doppiocdn.com")
            if swapped not in variants:
                variants.append(swapped)

    # LL-HLS parts can expire very quickly. Try non-part variant as fallback.
    for candidate in list(variants):
        if candidate.lower().endswith(".mp4") and "_part" in candidate.lower():
            without_part = re.sub(r"_part\d+(?=\.mp4(?:$|\?))", "", candidate, flags=re.IGNORECASE)
            if without_part != candidate and without_part not in variants:
                variants.append(without_part)

    return variants


def _rewrite_stripchat_m3u8_playlist(playlist_text: str, playlist_url: str) -> str:
    rewritten = []
    mouflon_queue = []

    for line in playlist_text.splitlines():
        stripped = line.strip()

        if stripped.startswith("#EXT-X-MOUFLON:URI:"):
            real_uri = stripped.split("#EXT-X-MOUFLON:URI:", 1)[1].strip()
            if real_uri:
                mouflon_queue.append(real_uri)
            rewritten.append(line)
            continue

        if stripped and not stripped.startswith("#") and stripped.lower().endswith("/media.mp4") and mouflon_queue:
            target = mouflon_queue.pop(0)
            rewritten.append(_to_stripchat_proxy_url(urljoin(playlist_url, target)))
            continue

        rewritten.append(rewrite_m3u8_line(line, playlist_url, "/api/stripchat/video_proxy"))

    return "\n".join(rewritten)


@stripchat_bp.route("/video_proxy")
def stripchat_video_proxy():
    target_url = request.args.get("url")
    if not target_url:
        return "Thiếu URL video Stripchat", 400

    is_m3u8 = ".m3u8" in target_url.lower().split("?")[0]

    headers = _build_media_headers()
    headers.update(
        {
            "priority": "u=1, i",
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }
    )

    range_header = request.headers.get("Range")
    if range_header:
        headers["range"] = range_header

    try:
        upstream = None
        attempts = _build_upstream_url_variants(target_url)

        for attempt_url in attempts:
            response = requests.get(attempt_url, headers=headers, stream=not is_m3u8, timeout=20)
            upstream = response

            # Keep retrying only when upstream returns a hard 404.
            if response.status_code != 404:
                break

        if upstream is None:
            return "Không lấy được dữ liệu upstream", 502

        if is_m3u8 or "mpegurl" in (upstream.headers.get("Content-Type", "").lower()):
            rewritten_playlist = _rewrite_stripchat_m3u8_playlist(upstream.text, upstream.url)
            return Response(
                rewritten_playlist,
                status=upstream.status_code,
                content_type="application/vnd.apple.mpegurl",
            )

        def generate():
            for chunk in upstream.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk

        response_headers = {}
        for key in ("Content-Length", "Accept-Ranges", "Content-Range"):
            value = upstream.headers.get(key)
            if value:
                response_headers[key] = value

        return Response(
            generate(),
            status=upstream.status_code,
            headers=response_headers,
            content_type=upstream.headers.get("Content-Type", "video/mp4"),
        )
    except Exception as exc:
        return str(exc), 500


@stripchat_bp.route("/config", methods=["POST"])
def update_stripchat_config():
    payload = request.json or {}
    if not isinstance(payload, dict):
        return jsonify({"code": -1, "msg": "Payload phải là JSON object"}), 400

    updated = {}
    for key, value in payload.items():
        if key not in _STRIPCHAT_CONFIG_KEYS or value is None:
            continue

        if key == "enabled":
            _stripchat_config[key] = bool(value)
        else:
            _stripchat_config[key] = str(value)
        updated[key] = _stripchat_config[key]

    if not updated:
        return jsonify(
            {
                "code": -1,
                "msg": "Không có key hợp lệ để cập nhật",
                "allowedKeys": sorted(list(_STRIPCHAT_CONFIG_KEYS)),
            }
        ), 400

    return jsonify({"code": 0, "msg": "Đã cập nhật cấu hình Stripchat", "updated": updated})


@stripchat_bp.route("/initial-config", methods=["GET"])
def get_stripchat_initial_config():
    try:
        initial = _fetch_stripchat_initial_config()
        payload = initial.get("data", {})
        websocket = ((payload.get("config") or {}).get("websocket") or {}) if isinstance(payload, dict) else {}

        return jsonify(
            {
                "code": 0 if initial.get("ok") else -1,
                "platform": "stripchat",
                "msg": "ok" if initial.get("ok") else "Không lấy được initial config",
                "data": {
                    "statusCode": initial.get("statusCode"),
                    "websocket": {
                        "url": websocket.get("url", ""),
                        "token": websocket.get("token", ""),
                    },
                    "raw": payload,
                },
            }
        ), initial.get("statusCode", 500)
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": {}}), 500


@stripchat_bp.route("/stream/<stream_id>/extensions", methods=["GET"])
def get_stripchat_stream_extensions(stream_id: str):
    stream_id = (stream_id or "").strip()
    if not stream_id:
        return jsonify({"code": -1, "msg": "Thiếu stream_id", "data": {}}), 400

    try:
        ext_result = _fetch_stripchat_stream_extensions(stream_id)
        payload = ext_result.get("data", {})
        return jsonify(
            {
                "code": 0 if ext_result.get("ok") else -1,
                "platform": "stripchat",
                "msg": "ok" if ext_result.get("ok") else "Không lấy được stream extensions",
                "data": {
                    "streamId": stream_id,
                    "statusCode": ext_result.get("statusCode"),
                    "channel": payload.get("channel", {}),
                    "extensions": payload.get("extensions", []),
                    "raw": payload,
                },
            }
        ), ext_result.get("statusCode", 500)
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": {}}), 500


@stripchat_bp.route("/idols", methods=["GET"])
def get_stripchat_idols():
    if not _stripchat_config["enabled"]:
        return jsonify({"code": -1, "msg": "Stripchat đang bị tắt trong cấu hình"}), 400

    params = {
        "primaryTag": request.args.get("primaryTag", "girls"),
        "limit": request.args.get("limit", default=24, type=int),
        "topLimit": request.args.get("topLimit", default=61, type=int),
        "favoritesLimit": request.args.get("favoritesLimit", default=24, type=int),
        "msBlock": _to_bool_arg("msBlock", True),
        "byw": _to_bool_arg("byw", False),
        "flags": request.args.get("flags", default=0, type=int),
        "srwm": _to_bool_arg("srwm", True),
        "rcmGrp": request.args.get("rcmGrp", "N"),
        "rbCnGr": _to_bool_arg("rbCnGr", True),
        "iem": _to_bool_arg("iem", True),
        "decMb": _to_bool_arg("decMb", True),
        "ctryTop": _to_bool_arg("ctryTop", True),
        "guestHash": request.args.get("guestHash", ""),
        "mlfv": _to_bool_arg("mlfv", False),
        "rectf": _to_bool_arg("rectf", False),
        "eab": _to_bool_arg("eab", False),
        "nic": _to_bool_arg("nic", True),
        "removeShows": _to_bool_arg("removeShows", True),
        "specialEventTagId": request.args.get("specialEventTagId", "worldTournament"),
        "uniq": request.args.get("uniq", ""),
    }
    params = {k: v for k, v in params.items() if v != ""}

    try:
        response = requests.get(
            _stripchat_config["base_url"],
            params=params,
            headers=_build_stripchat_headers(),
            timeout=20,
        )
        payload = response.json() if "json" in (response.headers.get("Content-Type", "").lower()) else {}
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": {"blocks": [], "records": []}}), 500

    blocks = payload.get("blocks", []) if isinstance(payload, dict) else []
    records = []

    for block in blocks:
        models = block.get("models", []) if isinstance(block, dict) else []
        for model in models:
            if not isinstance(model, dict):
                continue

            discovered_stream_urls = []
            _scan_for_stream_urls(model, discovered_stream_urls)
            avatar_url = _build_stripchat_avatar_url(model)

            records.append(
                {
                    "platform": "stripchat",
                    "blockId": block.get("id", ""),
                    "blockUrl": block.get("url", ""),
                    "nickname": model.get("username", ""),
                    "username": model.get("username", ""),
                    "profileUrl": f"{_stripchat_config['site_origin'].rstrip('/')}/{model.get('username', '')}",
                    "id": model.get("id"),
                    "avatar": avatar_url,
                    "avatarUrl": avatar_url,
                    "avatarProfileUrl": _to_absolute_media_url(model.get("avatarUrl", "")),
                    "previewUrlThumbSmall": _to_absolute_media_url(model.get("previewUrlThumbSmall", "")),
                    "country": model.get("country", ""),
                    "gender": model.get("gender", ""),
                    "broadcastGender": model.get("broadcastGender", ""),
                    "status": model.get("status", ""),
                    "isLive": bool(model.get("isLive")),
                    "isOnline": bool(model.get("isOnline")),
                    "viewersCount": model.get("viewersCount", 0),
                    "privateRate": model.get("privateRate", 0),
                    "p2pRate": model.get("p2pRate", 0),
                    "spyRate": model.get("spyRate", 0),
                    "isNew": bool(model.get("isNew")),
                    "isHd": bool(model.get("isHd")),
                    "isMobile": bool(model.get("isMobile")),
                    "isLovense": bool(model.get("isLovense")),
                    "presets": model.get("presets") or [],
                    "presetsAv1": model.get("presetsAv1") or [],
                    "streamName": model.get("streamName", ""),
                    "hlsPlaylist": _normalize_hls_url(model.get("hlsPlaylist", "")),
                    "streamUrl": discovered_stream_urls[0] if discovered_stream_urls else "",
                    "statusChangedAt": model.get("statusChangedAt", ""),
                }
            )

    return jsonify(
        {
            "code": 0,
            "platform": "stripchat",
            "msg": "ok",
            "data": {"blocks": blocks, "records": records, "total": len(records)},
        }
    ), response.status_code


@stripchat_bp.route("/stream-info", methods=["POST"])
def get_stripchat_stream_info():
    started_at = time.monotonic()

    def out_of_budget() -> bool:
        return (time.monotonic() - started_at) >= _STREAM_INFO_BUDGET_SEC

    data = request.json or {}
    username = str(data.get("username") or "").strip()
    stream_name = str(data.get("streamName") or "").strip()
    model_id = str(data.get("id") or "").strip()
    input_hls_playlist = _normalize_hls_url(str(data.get("hlsPlaylist") or "").strip())

    stream_id = str(data.get("streamId") or "").strip() or model_id or stream_name
    if not stream_id:
        stream_id = _extract_stream_id_from_hls_url(input_hls_playlist)

    if not username and not stream_name:
        return jsonify({"code": -1, "msg": "Thiếu username hoặc streamName"}), 400

    candidates = []
    profile_candidates = []
    related_candidates = []
    pkey_candidates = []
    token_pool = []

    if input_hls_playlist:
        candidates.append(input_hls_playlist)

    initial = _fetch_stripchat_initial_config()
    initial_payload = initial.get("data", {})
    websocket = ((initial_payload.get("config") or {}).get("websocket") or {}) if isinstance(initial_payload, dict) else {}
    initial_config_meta = {
        "ok": bool(initial.get("ok")),
        "statusCode": initial.get("statusCode"),
        "websocket": {
            "url": websocket.get("url", ""),
            "token": websocket.get("token", ""),
        },
    }

    stream_extensions_meta = {
        "ok": False,
        "statusCode": None,
        "streamId": stream_id,
        "channel": {},
        "extensions": [],
    }
    if stream_id and not out_of_budget():
        ext = _fetch_stripchat_stream_extensions(stream_id)
        ext_payload = ext.get("data", {})
        stream_extensions_meta = {
            "ok": bool(ext.get("ok")),
            "statusCode": ext.get("statusCode"),
            "streamId": stream_id,
            "channel": ext_payload.get("channel", {}) if isinstance(ext_payload, dict) else {},
            "extensions": ext_payload.get("extensions", []) if isinstance(ext_payload, dict) else [],
        }

    if not out_of_budget():
        related_candidates = _fetch_related_hls_candidates(stream_id=stream_id, username=username)
    for item in related_candidates:
        if item not in candidates:
            candidates.append(item)

    profile_html = ""
    if username and not out_of_budget():
        profile_url = f"{_stripchat_config['site_origin'].rstrip('/')}/{username}"
        headers = _build_stripchat_headers()
        headers["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        try:
            resp = requests.get(profile_url, headers=headers, timeout=_HTTP_TIMEOUT_FAST)
            profile_html = resp.text or ""
        except Exception as exc:
            return jsonify(
                {
                    "code": -1,
                    "msg": f"Không lấy được trang profile: {exc}",
                    "data": {"streamUrl": "", "streamCandidates": candidates},
                }
            ), 500

    if profile_html:
        profile_candidates = _extract_m3u8_urls_from_html(profile_html)
        for item in profile_candidates:
            if item not in candidates:
                candidates.append(item)

        if not stream_id:
            stream_id = _extract_stream_id_from_html(profile_html, username)
            if not stream_id:
                stream_id = _extract_stream_id_from_hls_url(_pick_best_hls_candidate(profile_candidates))

        pkeys_from_html = _extract_pkeys_from_html(profile_html)
        for token in pkeys_from_html:
            if token not in token_pool:
                token_pool.append(token)

        canonical = _build_canonical_pkey_candidates(candidates, stream_id, token_pool)
        for item in canonical:
            if item not in pkey_candidates:
                pkey_candidates.append(item)
            if item not in candidates:
                candidates.append(item)

    filtered_candidates = _filter_candidates_by_stream_id(candidates, stream_id)

    if not out_of_budget():
        expanded_candidates, tokens_from_playlists = _expand_hls_candidates(filtered_candidates, max_depth=1)
        for token in tokens_from_playlists:
            if token not in token_pool:
                token_pool.append(token)

        for item in expanded_candidates:
            if item not in filtered_candidates:
                filtered_candidates.append(item)
            if item not in candidates:
                candidates.append(item)

    canonical_from_expansion = _build_canonical_pkey_candidates(filtered_candidates, stream_id, token_pool)
    for item in canonical_from_expansion:
        if item not in pkey_candidates:
            pkey_candidates.append(item)
        if item not in filtered_candidates:
            filtered_candidates.append(item)

    for item in filtered_candidates:
        if "pkey=" in (item or "").lower() and item not in pkey_candidates:
            pkey_candidates.append(item)

    raw_stream_url = _pick_best_hls_candidate(pkey_candidates)
    if not raw_stream_url:
        raw_stream_url = _pick_best_hls_candidate(filtered_candidates)

    stream_url = _to_playback_hls_url(raw_stream_url)

    if stream_url and not out_of_budget() and _is_ad_playlist(stream_url):
        replacement = _pick_best_hls_candidate(pkey_candidates)
        if replacement:
            stream_url = _to_playback_hls_url(replacement)
            raw_stream_url = replacement

    ad_only = bool(stream_url and not out_of_budget() and _is_ad_playlist(stream_url))
    room_config = _build_room_config_output(
        username=username,
        stream_id=stream_id,
        primary_stream=stream_url,
        pkey_candidates=pkey_candidates,
        initial_config_meta=initial_config_meta,
    )

    return jsonify(
        {
            "code": 0 if (stream_url and not ad_only) else -1,
            "platform": "stripchat",
            "msg": "ok" if (stream_url and not ad_only) else "Model hiện không có live stream hợp lệ (chỉ có clip quảng cáo/VOD)",
            "data": {
                "username": username,
                "streamUrl": stream_url,
                "rawStreamUrl": raw_stream_url,
                "streamCandidates": candidates,
                "preferredCandidates": filtered_candidates,
                "pkeyCandidates": pkey_candidates,
                "relatedCandidates": related_candidates,
                "profileCandidates": profile_candidates,
                "tokenCandidates": token_pool,
                "adOnly": ad_only,
                "roomConfig": room_config,
                "resolvedByStreamId": stream_id,
                "initialConfig": initial_config_meta,
                "streamExtensions": stream_extensions_meta,
            },
        }
    )


@stripchat_bp.route("/health", methods=["GET"])
def stripchat_health():
    return jsonify(
        {
            "code": 0,
            "platform": "stripchat",
            "status": "ready",
            "config": {
                "hasBaseUrl": bool(_stripchat_config["base_url"]),
                "hasCookie": bool(_stripchat_config["cookie"]),
                "enabled": bool(_stripchat_config["enabled"]),
            },
        }
    )


@stripchat_bp.route("/iframe-player", methods=["GET"])
def stripchat_iframe_player():
    return render_template("stripchat_iframe_player.html")
