import base64
import time
from urllib.parse import unquote

import requests
from Crypto.Cipher import AES
from flask import Blueprint, Response, jsonify, request

from platforms.common import rewrite_m3u8_line

yylive_bp = Blueprint("yylive", __name__, url_prefix="/api/yylive")

_yylive_config = {
    "authorization": "Bearer eyJraWQiOiJ1c2VyLW9hdXRoMi1qa3MiLCJhbGciOiJSUzI1NiJ9.eyJhZ2VudElkIjoyMDU4ODE2MzEwOTg4MjkyMDk3LCJtZXJjaGFudElkIjo1MTEsInVzZXJfbmFtZSI6IjJ2NXNuOXpzZGR2bCIsInNjb3BlIjpbImFsbCJdLCJtZW1iZXJUeXBlIjoxLCJleHAiOjE3ODQ2MjMwOTMsImp0aSI6ImUzNDFmNDE0LWI3YTAtNGMyNi1hMmUwLWM1ZDVjYTRhNDZlMyIsImNsaWVudF9pZCI6IndlYi1wbGF5ZXIiLCJhdXRob3JpdGllcyI6WyJST0xFX1dFQl9QTEFZRVIiXSwidXNlcm5hbWUiOiIydjVzbjl6c2RkdmwiLCJtZW1iZXJJZCI6IjIwNzQwNTAyNzI1NTE5MzYwMDIifQ.lFBZOKnY6PxtYp2BgVF4bGSaKOK14jWtvnSUU5OA1HIi_dVSwTLHS6Nte2Yu8K4RWhx830DceDaaT963dBoQWmSS3p4aQbZWPnZSKyOqLk2Cx8Zn-FdW46myglWZqpRTRRq0AoyhhmoZlnoFJn1A__X2JYY3T02VZqslOGm49qZmxVQARtCROr7ySk2JOrnba3I0LROV7JVcdteSwKBLS7lx08GRBAHbqPJJ-3NRNJMZhPctzbDo_OGcmNhR5lZPYqdfYVnM4AslcL3Q4liNzQ0gz0QShZ8JvAYJtOK1Z7TfuR3fgLsYYeCJ5kuoHZaxpn4cqtIzjNuxlzj9W3VlxA",
    "device": "abb7c99e-3b6e-4707-a31b-0673e2306542",
    "sign": "11f569ed792da4e0cff8a393534a5bf2",
    "room_cr_sign": "8342aef9022ab16ab6f96cfe00fadf62",
    "merchant_id": "511",
    "area": "VN",
    "time_zone": "GMT+07:00",
    "locale_language": "VIT",
    "system_version": "1.5.1",
    "version_code": "101",
}

_YYLIVE_CONFIG_KEYS = {
    "authorization",
    "device",
    "sign",
    "room_cr_sign",
    "merchant_id",
    "area",
    "time_zone",
    "locale_language",
    "system_version",
    "version_code",
}

_YY_AES_NEW_KEY = "9216345272696329"
_YY_AES_NEW_IV = "0507060302080104"
_YY_AES_OLD_KEY = "1558668820991598"
_YY_AES_OLD_IV = "0102030405060708"
_YY_STREAM_KEY = "star@livega*963."
_YY_STREAM_IV = "0608040307010502"

_YY_STREAM_FIELD_KEYS = (
    "pullAddr",
    "pullAddr265",
    "unlDefPa",
    "unlLowPa",
    "unlDefPa265",
    "unlLowPa265",
    "liveAddress",
    "liveAddress265",
    # Keys in raw room-info payload before frontend remap.
    "la",
)



def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        return data
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return data
    return data[:-pad_len]



def _yy_decrypt_b64_aes_cbc(cipher_text: str, key: str, iv: str) -> str:
    if not cipher_text:
        return ""
    try:
        cipher_bytes = base64.b64decode(cipher_text)
        aes = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        decrypted = aes.decrypt(cipher_bytes)
        plaintext = _pkcs7_unpad(decrypted).decode("utf-8", errors="ignore").strip()
        return plaintext
    except Exception:
        return ""



def _looks_like_url(value: str) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))



def _is_probable_stream_url(value: str) -> bool:
    if not _looks_like_url(value):
        return False
    lowered = value.lower()
    return any(token in lowered for token in (".m3u8", ".flv", ".ts", "live", "stream"))


def _decrypt_yylive_stream_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return _yy_decrypt_b64_aes_cbc(value, _YY_STREAM_KEY, _YY_STREAM_IV)



def _push_candidate(candidates: list, value: str) -> None:
    if not value or not isinstance(value, str):
        return

    values = [value, unquote(value)]
    for candidate in values:
        if candidate and candidate not in candidates:
            candidates.append(candidate)



def _scan_for_urls(payload, candidates: list) -> None:
    if payload is None:
        return

    if isinstance(payload, str):
        text = payload.strip()
        if _looks_like_url(text):
            _push_candidate(candidates, text)
        return

    if isinstance(payload, list):
        for item in payload:
            _scan_for_urls(item, candidates)
        return

    if isinstance(payload, dict):
        for value in payload.values():
            _scan_for_urls(value, candidates)



def _collect_stream_url_candidates(room_info_data: dict, room_cr_result: dict) -> list:
    candidates = []
    _scan_for_urls(room_info_data, candidates)

    if isinstance(room_cr_result, dict):
        _scan_for_urls(room_cr_result.get("data"), candidates)

    if isinstance(room_info_data, dict):
        for key in _YY_STREAM_FIELD_KEYS:
            raw_value = room_info_data.get(key)
            if not isinstance(raw_value, str) or not raw_value:
                continue

            decrypted = _decrypt_yylive_stream_url(raw_value)
            if _looks_like_url(decrypted):
                _push_candidate(candidates, decrypted)

    return candidates



def _pick_best_stream_url(candidates: list) -> str:
    stream_candidates = [item for item in candidates if _is_probable_stream_url(item)]

    for item in stream_candidates:
        if ".m3u8" in item.lower():
            return item

    if stream_candidates:
        return stream_candidates[0]

    for item in candidates:
        if _looks_like_url(item):
            return item

    return ""



def _build_yylive_headers(include_auth: bool, content_type: str = None, sign: str = None) -> dict:
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi",
        "area": _yylive_config["area"],
        "device": _yylive_config["device"],
        "locale-language": _yylive_config["locale_language"],
        "merchantid": _yylive_config["merchant_id"],
        "sign": sign or _yylive_config["sign"],
        "system-version": _yylive_config["system_version"],
        "time-zone": _yylive_config["time_zone"],
        "versioncode": _yylive_config["version_code"],
        "origin": "https://71234.tv",
        "referer": "https://71234.tv/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "dev-type": "H5",
    }

    if include_auth:
        headers["authorization"] = _yylive_config["authorization"]

    if content_type:
        headers["content-type"] = content_type

    return headers


@yylive_bp.route("/rooms", methods=["GET"])
def get_yylive_rooms():
    page_num = request.args.get("pageNum", default=1, type=int)
    page_size = request.args.get("pageSize", default=50, type=int)

    url = "https://sw.fnccdn.com/511/api/plr/h5/v3/public/live/lrl.json"
    query = {
        "pageNum": page_num,
        "pageSize": page_size,
        "merchantId": "511",
        "area": "VN",
        "lang": "VIT",
        "t": str(int(time.time())),
    }

    headers = _build_yylive_headers(include_auth=False)

    try:
        response = requests.get(url, headers=headers, params=query, timeout=12)
        return jsonify(response.json()), response.status_code
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": {"records": []}}), 500


@yylive_bp.route("/config", methods=["POST"])
def update_yylive_config():
    payload = request.json or {}
    if not isinstance(payload, dict):
        return jsonify({"code": -1, "msg": "Payload phải là JSON object"}), 400

    updated = {}
    for key, value in payload.items():
        if key in _YYLIVE_CONFIG_KEYS and value is not None:
            _yylive_config[key] = str(value)
            updated[key] = _yylive_config[key]

    if not updated:
        return jsonify(
            {
                "code": -1,
                "msg": "Không có key hợp lệ để cập nhật",
                "allowedKeys": sorted(list(_YYLIVE_CONFIG_KEYS)),
            }
        ), 400

    return jsonify({"code": 0, "msg": "Đã cập nhật cấu hình YYLive", "updated": updated})


@yylive_bp.route("/room-info", methods=["POST"])
def get_yylive_room_info():
    data = request.json or {}
    anchor_id = data.get("anchorId")
    live_id = data.get("liveId")

    if not anchor_id:
        return jsonify({"code": -1, "msg": "Thiếu anchorId"}), 400

    url = "https://sw.fnccdn.com/511/api/plr/zbliv/h5/v3/public/live/room-info"
    payload = {"anchorId": str(anchor_id), "isSupportH265": True}

    headers = _build_yylive_headers(include_auth=True, content_type="application/json")

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        result = response.json()

        room_cr_result = None
        if live_id:
            room_cr_url = "https://sw.fnccdn.com/511/api/plr/zbliv/h5/v3/public/live/room-cr"
            room_cr_query = {"liveId": str(live_id), "isSupportH265": "true"}
            room_cr_headers = _build_yylive_headers(
                include_auth=True,
                sign=_yylive_config["room_cr_sign"],
            )
            room_cr_headers["content-length"] = "0"
            room_cr_response = requests.post(
                room_cr_url,
                headers=room_cr_headers,
                params=room_cr_query,
                timeout=12,
            )
            room_cr_result = room_cr_response.json()

        if result.get("code") == 200 and isinstance(result.get("data"), dict):
            data_obj = result["data"]
            pull_addr = data_obj.get("pullAddr", "")
            pull_new = _yy_decrypt_b64_aes_cbc(pull_addr, _YY_AES_NEW_KEY, _YY_AES_NEW_IV)
            pull_old = _yy_decrypt_b64_aes_cbc(pull_addr, _YY_AES_OLD_KEY, _YY_AES_OLD_IV)
            pull_stream = _decrypt_yylive_stream_url(pull_addr)

            pull_candidates = _collect_stream_url_candidates(data_obj, room_cr_result)
            if _looks_like_url(pull_stream):
                _push_candidate(pull_candidates, pull_stream)
            if _looks_like_url(pull_new):
                _push_candidate(pull_candidates, pull_new)
            if _looks_like_url(pull_old):
                _push_candidate(pull_candidates, pull_old)
            if _looks_like_url(pull_addr):
                _push_candidate(pull_candidates, pull_addr)

            best_stream_url = _pick_best_stream_url(pull_candidates)
            stream_candidates = [u for u in pull_candidates if _is_probable_stream_url(u)]

            data_obj["streamUrl"] = best_stream_url
            data_obj["pullUrlCandidates"] = pull_candidates
            data_obj["streamCandidates"] = stream_candidates
            data_obj["decodedPullAddr"] = pull_stream

            if not best_stream_url:
                room_cr_meta = ""
                if isinstance(room_cr_result, dict) and isinstance(room_cr_result.get("data"), list):
                    entries = room_cr_result.get("data", [])
                    room_cr_meta = "; ".join(
                        [
                            f"codeRate={x.get('codeRate')} vipGrade={x.get('vipGrade')}"
                            for x in entries
                            if isinstance(x, dict)
                        ]
                    )
                data_obj["streamError"] = (
                    "Không tìm thấy URL stream từ room-info/room-cr. "
                    "Có thể sign/token đã hết hạn hoặc cơ chế mã hóa pullAddr đã thay đổi. "
                    f"room-cr: {room_cr_meta or 'không có dữ liệu chi tiết'}"
                )

            if room_cr_result and room_cr_result.get("code") == 200 and isinstance(room_cr_result.get("data"), list):
                data_obj["roomCr"] = room_cr_result.get("data", [])

        result["roomCrResult"] = room_cr_result
        return jsonify(result), response.status_code
    except Exception as exc:
        return jsonify({"code": -1, "msg": str(exc), "data": {}}), 500


@yylive_bp.route("/video_proxy")
def yylive_video_proxy():
    target_url = request.args.get("url")
    if not target_url:
        return "Thiếu đường dẫn video YY", 400

    headers = {
        "accept": "*/*",
        "accept-language": "vi",
        "cache-control": "no-cache",
        "dnt": "1",
        "origin": "https://71234.tv",
        "pragma": "no-cache",
        "referer": "https://71234.tv/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }

    try:
        is_m3u8 = target_url.lower().split("?")[0].endswith(".m3u8")
        req = requests.get(target_url, headers=headers, stream=not is_m3u8, timeout=15)

        if is_m3u8 or "mpegurl" in (req.headers.get("Content-Type", "").lower()):
            rewritten_lines = [
                rewrite_m3u8_line(line, req.url, "/api/yylive/video_proxy")
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
            status=req.status_code,
            content_type=req.headers.get("Content-Type", "video/mp2t"),
        )
    except Exception as exc:
        return str(exc), 500
