import requests
from flask import Flask, jsonify, render_template, request, Response

app = Flask(__name__)

# Lưu trữ tạm thời cấu hình token động gửi từ client lên để dùng chung cho cả 2 API
current_config = {
    "authorization": "HSBox eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIwNTgyMTE3OTg4IiwidXNlcklkIjoyMDI2NTgwODY5LCJzeXN0ZW0iOjE3ODMzMjMxMzEyOTYsInVkaWQiOiI2ZWEyN2I0NTE4NWVlNTVhZWRkZDdjZDI5ZTUyYWM3MSIsInNhdCI6MCwiaXNzIjoiTVhDZW50ZXIiLCJleHAiOjE3ODM5Mjc5MzF9.z5Kl3Eh3t848h7mTYI8kzX-iKBjLIe3lpUZlRF-q6qI",
    "x-sign": "ad892cd48caf19e914f583faec60bc5a", # Sẽ tự động cập nhật khi người dùng nhấn đồng bộ
    "x-timestamp": "1783323981066",
    "uid": 2026580869
}

@app.route("/")
def index():
    return render_template("index.html")
@app.route("/api/video_proxy")
def video_proxy():
    # Lấy link flv gốc truyền từ frontend lên
    target_url = request.args.get("url")
    if not target_url:
        return "Thiếu đường dẫn luồng phát", 400

    # Đảm bảo link gọi đi là http thường để kết nối đến CDN gốc của họ
    target_url = target_url.replace("https://", "http://")

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://mm-live.online/",
    }

    try:
        # Gọi lên CDN gốc, bật stream=True để đọc dữ liệu liên tục
        req = requests.get(target_url, headers=headers, stream=True, timeout=15)

        # Trả luồng dữ liệu về cho trình duyệt từng chút một (chunk-by-chunk)
        def generate():
            for chunk in req.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk

        return Response(
            generate(), content_type="video/x-flv", status=req.status_code
        )

    except Exception as e:
        return str(e), 500
@app.route("/api/idols", methods=["GET"])
def get_idols():
    global current_config

    url = "https://gateway.mm-live.online/live-client/live/new/4231/1529/list"
    payload = {"uid": int(current_config["uid"]), "type": 1, "os": 0}
    
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "VI",
        "appid": "MMLive",
        "authorization": current_config["authorization"],
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://mm-live.online",
        "referer": "https://mm-live.online/",
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
        "x-appversion": "2.7.1",
        "x-language": "VI",
        "x-sign": current_config["x-sign"],
        "x-timestamp": str(current_config["x-timestamp"]),
        "x-udid": "6ea27b45185ee55aeddd7cd29e52ac71"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e), "data": []}), 500

# NEW: API LẤY LINK STREAM THEO IDOL
@app.route("/api/stream", methods=["POST"])
def get_stream():
    global current_config
    data = request.json or {}
    anchor_id = data.get("anchorId")
    live_id = data.get("liveId")

    if not anchor_id or not live_id:
        return jsonify({"code": -1, "msg": "Thiếu thông tin Idol"}), 400

    url = "https://gateway.mm-live.online/live-client/live/inter/room/220"
    
    # Tạo payload động theo Idol được click
    payload = {
        "anchorId": int(anchor_id),
        "liveId": int(live_id),
        "os": 0,
        "uid": int(current_config["uid"]),
        "type": 0,
        "liveStatus": "1",
        "isPreview": 0
    }

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "VI",
        "appid": "MMLive",
        "authorization": current_config["authorization"],
        "content-type": "application/json;charset=UTF-8",
        "new-pk": "1",
        "origin": "https://mm-live.online",
        "referer": "https://mm-live.online/",
        "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
        "x-appversion": "2.7.1",
        "x-language": "VI",
        "x-sign": current_config["x-sign"],
        "x-timestamp": str(current_config["x-timestamp"]),
        "x-udid": "6ea27b45185ee55aeddd7cd29e52ac71"
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)