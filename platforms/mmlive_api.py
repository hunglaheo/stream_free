import datetime
import base64
import hashlib
import json
import os
import time

import requests
from flask import Blueprint, Response, jsonify, request
from urllib.parse import urlparse

from platforms.common import rewrite_m3u8_line

mmlive_bp = Blueprint("mmlive", __name__, url_prefix="/api/mmlive")

_mmlive_config = {
    "authorization": os.getenv(
        "MMLIVE_AUTHORIZATION",
        "HSBox eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIwNTgyMTE3OTg4IiwidXNlcklkIjoyMDI2NTgwODY5LCJzeXN0ZW0iOjE3ODMzMjMxMzEyOTYsInVkaWQiOiI2ZWEyN2I0NTE4NWVlNTVhZWRkZDdjZDI5ZTUyYWM3MSIsInNhdCI6MCwiaXNzIjoiTVhDZW50ZXIiLCJleHAiOjE3ODM5Mjc5MzF9.z5Kl3Eh3t848h7mTYI8kzX-iKBjLIe3lpUZlRF-q6qI",
    ),
    "x_udid": os.getenv("MMLIVE_X_UDID", "6ea27b45185ee55aeddd7cd29e52ac71"),
    "x_appversion": os.getenv("MMLIVE_X_APPVERSION", "2.7.1"),
    "x_language": os.getenv("MMLIVE_X_LANGUAGE", "VI"),
    "uid": 2026580869,
}

if os.getenv("MMLIVE_UID"):
    try:
        _mmlive_config["uid"] = int(os.getenv("MMLIVE_UID", "0"))
    except ValueError:
        pass



def _build_x_sign(xudid: str, now_time: str) -> str:
    input_string = f"{xudid}jgyh,kasd{now_time}"
    return hashlib.md5(input_string.encode()).hexdigest()


def _decode_jwt_payload(auth_header: str) -> dict:
    if not auth_header:
        return {}

    token = auth_header.strip()
    if " " in token:
        token = token.split(" ", 1)[1].strip()

    parts = token.split(".")
    if len(parts) != 3:
        return {}

    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        result = json.loads(decoded)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _authorization_expiry_epoch() -> int:
    payload = _decode_jwt_payload(str(_mmlive_config.get("authorization", "")))
    exp = payload.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else 0


def _is_authorization_expired(leeway_seconds: int = 15) -> bool:
    exp = _authorization_expiry_epoch()
    return bool(exp and time.time() >= exp - max(0, leeway_seconds))


def _is_auth_related_message(message: str) -> bool:
    lowered = (message or "").lower()
    return any(
        token in lowered
        for token in (
            "token",
            "authorization",
            "x-sign",
            "x sign",
            "signature",
            "expired",
            "login",
            "unauthorized",
            "forbidden",
        )
    )


def _is_room_type_changed_message(message: str) -> bool:
    lowered = (message or "").lower()
    return any(
        token in lowered
        for token in (
            "room type",
            "loai phong",
            "loại phòng",
            "phong da thay doi",
            "phòng đã thay đổi",
            "room changed",
            "room has changed",
            "room status changed",
            "房间类型",
            "房间已变更",
        )
    )


def _is_success_stream_response(result: dict) -> bool:
    return isinstance(result, dict) and int(result.get("code", -1)) == 0 and bool(result.get("data"))


def _request_stream_with_fallback(anchor_id: int, live_id: int, room_type=None) -> dict:
    url = "https://gateway.mm-live.online/live-client/live/inter/room/220"
    base_payload = {
        "anchorId": int(anchor_id),
        "liveId": int(live_id),
        "os": 0,
        "uid": int(_mmlive_config["uid"]),
    }

    first_payload = {
        **base_payload,
        "type": 0,
        "liveStatus": "1",
        "isPreview": 0,
    }
    first_result = _post_mm_api(url, first_payload, include_new_pk=True)
    if _is_success_stream_response(first_result):
        return first_result

    first_msg = str(first_result.get("msg", "")) if isinstance(first_result, dict) else ""
    if not _is_room_type_changed_message(first_msg):
        return first_result

    # Room mode can rotate dynamically; retry with alternate combinations.
    candidate_types = []
    if isinstance(room_type, (int, str)):
        try:
            candidate_types.append(int(room_type))
        except (TypeError, ValueError):
            pass
    candidate_types.extend([0, 1, 2, 3, 4])

    unique_types = []
    for value in candidate_types:
        if value not in unique_types:
            unique_types.append(value)

    variants = []
    for tp in unique_types:
        variants.append({**base_payload, "type": tp, "liveStatus": "1", "isPreview": 0})
        variants.append({**base_payload, "type": tp, "liveStatus": 1, "isPreview": 0})
        variants.append({**base_payload, "type": tp, "isPreview": 0})
        variants.append({**base_payload, "type": tp})

    seen = set()
    for payload in variants:
        key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        result = _post_mm_api(url, payload, include_new_pk=True)
        if _is_success_stream_response(result):
            result["fallbackApplied"] = True
            result["usedPayload"] = payload
            return result

    first_result["roomTypeFallbackTried"] = True
    first_result["hint"] = "MM Live bao loai phong da thay doi; da thu payload fallback nhung chua lay duoc stream"
    return first_result


def _build_mm_headers(now_time: str, *, include_new_pk: bool = False) -> dict:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "VI",
        "appid": "MMLive",
        "authorization": str(_mmlive_config.get("authorization", "")).strip(),
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://mm-live.online",
        "referer": "https://mm-live.online/",
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
        "x-appversion": str(_mmlive_config.get("x_appversion", "2.7.1")),
        "x-language": str(_mmlive_config.get("x_language", "VI")),
        "x-sign": _build_x_sign(str(_mmlive_config.get("x_udid", "")), now_time),
        "x-timestamp": now_time,
        "x-udid": str(_mmlive_config.get("x_udid", "")),
    }
    if include_new_pk:
        headers["new-pk"] = "1"
    return headers


def _post_mm_api(url: str, payload: dict, *, include_new_pk: bool = False, timeout: int = 10):
    now_time = str(int(datetime.datetime.now().timestamp()))
    headers = _build_mm_headers(now_time, include_new_pk=include_new_pk)
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        body = response.json()
    except Exception:
        return {
            "code": -1,
            "msg": "MM Live tra ve du lieu khong phai JSON.",
            "httpStatus": response.status_code,
        }

    if isinstance(body, dict):
        return body

    return {
        "code": -1,
        "msg": "MM Live tra ve JSON khong hop le.",
        "httpStatus": response.status_code,
    }


@mmlive_bp.route("/config", methods=["GET", "POST"])
def mmlive_config():
    if request.method == "GET":
        exp = _authorization_expiry_epoch()
        return jsonify(
            {
                "code": 0,
                "data": {
                    "uid": _mmlive_config.get("uid"),
                    "xUdid": _mmlive_config.get("x_udid"),
                    "xAppVersion": _mmlive_config.get("x_appversion"),
                    "xLanguage": _mmlive_config.get("x_language"),
                    "authorizationExpired": _is_authorization_expired(),
                    "authorizationExp": exp,
                },
            }
        )

    data = request.json or {}
    updated = {}

    if "authorization" in data and str(data.get("authorization", "")).strip():
        _mmlive_config["authorization"] = str(data.get("authorization", "")).strip()
        updated["authorization"] = True

    if "uid" in data:
        try:
            _mmlive_config["uid"] = int(data.get("uid"))
            updated["uid"] = _mmlive_config["uid"]
        except (TypeError, ValueError):
            return jsonify({"code": -1, "msg": "uid khong hop le"}), 400

    if "xUdid" in data and str(data.get("xUdid", "")).strip():
        _mmlive_config["x_udid"] = str(data.get("xUdid", "")).strip()
        updated["xUdid"] = _mmlive_config["x_udid"]

    if "xAppVersion" in data and str(data.get("xAppVersion", "")).strip():
        _mmlive_config["x_appversion"] = str(data.get("xAppVersion", "")).strip()
        updated["xAppVersion"] = _mmlive_config["x_appversion"]

    if "xLanguage" in data and str(data.get("xLanguage", "")).strip():
        _mmlive_config["x_language"] = str(data.get("xLanguage", "")).strip()
        updated["xLanguage"] = _mmlive_config["x_language"]

    if not updated:
        return jsonify({"code": -1, "msg": "Khong co gia tri nao duoc cap nhat"}), 400

    exp = _authorization_expiry_epoch()
    return jsonify(
        {
            "code": 0,
            "msg": "Da cap nhat cau hinh MMLive",
            "data": {
                "updated": updated,
                "authorizationExpired": _is_authorization_expired(),
                "authorizationExp": exp,
            },
        }
    )


@mmlive_bp.route("/video_proxy")
def video_proxy():
    target_url = request.args.get("url")
    if not target_url:
        return "Thiếu đường dẫn luồng phát", 400

    is_m3u8 = target_url.lower().split("?")[0].endswith(".m3u8")
    request_url = target_url if is_m3u8 else target_url.replace("https://", "http://")

    parsed_target = urlparse(request_url)
    source_origin = (
        f"{parsed_target.scheme}://{parsed_target.netloc}"
        if parsed_target.scheme and parsed_target.netloc
        else "https://mm-live.online"
    )

    headers = {
        "accept": "*/*",
        "accept-language": "vi,en-US;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
        "Origin": source_origin,
        "Referer": f"{source_origin}/",
    }

    try:
        req = requests.get(request_url, headers=headers, stream=not is_m3u8, timeout=15)

        if is_m3u8 or "mpegurl" in (req.headers.get("Content-Type", "").lower()):
            rewritten_lines = [
                rewrite_m3u8_line(line, req.url, "/api/mmlive/video_proxy")
                for line in req.text.splitlines()
            ]
            rewritten_playlist = "\n".join(rewritten_lines)
            return Response(
                rewritten_playlist,
                status=req.status_code,
                content_type="application/vnd.apple.mpegurl",
            )

        def generate():
            for chunk in req.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk

        return Response(
            generate(),
            content_type=req.headers.get("Content-Type", "video/x-flv"),
            status=req.status_code,
        )
    except Exception as exc:
        return str(exc), 500


@mmlive_bp.route("/idols", methods=["GET"])
def get_idols():
    url = "https://gateway.mm-live.online/live-client/live/new/4231/1529/list"
    payload = {"uid": int(_mmlive_config["uid"]), "type": 1, "os": 0}

    try:
        result = _post_mm_api(url, payload)
        msg = str(result.get("msg", "")) if isinstance(result, dict) else ""
        if _is_authorization_expired() or _is_auth_related_message(msg):
            result["authExpired"] = True
            if not msg:
                result["msg"] = "Authorization MM Live da het han, hay cap nhat token moi"
        return jsonify(result)
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": []}), 500


@mmlive_bp.route("/stream", methods=["POST"])
def get_stream():
    data = request.json or {}
    anchor_id = data.get("anchorId")
    live_id = data.get("liveId")
    room_type = data.get("roomType")

    if not anchor_id or not live_id:
        return jsonify({"code": -1, "msg": "Thiếu thông tin Idol"}), 400

    try:
        result = _request_stream_with_fallback(int(anchor_id), int(live_id), room_type=room_type)
        msg = str(result.get("msg", "")) if isinstance(result, dict) else ""
        if _is_authorization_expired() or _is_auth_related_message(msg):
            result["authExpired"] = True
            if not msg:
                result["msg"] = "Authorization MM Live da het han, hay cap nhat token moi"
        return jsonify(result)
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc)}), 500
