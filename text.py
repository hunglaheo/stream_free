from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# Mock data được trích xuất chính xác từ file JSON traffic của bạn
MOCK_IDOLS = [
    {
        "anchorId": "1787831695819378689",
        "anchorNickname": "🦜SOI CẦU TÀI XỈU 🎲",
        "liveName": "🦜SOI CẦU TÀI XỈU 🎲",
        "coverUrl": "https://img.y3cdn.com/anchor-photo/20260609/f5aa24b68c7b4de5aad3dc763a7eb132.jpg",
        "onlineCount": 32241
    },
    {
        "anchorId": "1872165888673222657",
        "anchorNickname": "🐕HÚP_TỨ.🐝",
        "liveName": "🐕HÚP_TỨ.🐝",
        "coverUrl": "https://img.y3cdn.com/20260504/db2e4c08550e4594b6387cf06a92dc4a.jpg",
        "onlineCount": 31667
    },
    {
        "anchorId": "1900493761044578305",
        "anchorNickname": "🦜SC1MWINGO",
        "liveName": "8386 NHA",
        "coverUrl": "https://img.y3cdn.com/anchor-photo/20260513/499d01a572fd4315be9925f48fe5f893.jpg",
        "onlineCount": 4766
    },
    {
        "anchorId": "1598549452099416065",
        "anchorNickname": "🐕SCSicBo",
        "liveName": "🐕SCSicBo",
        "coverUrl": "https://img.y3cdn.com/anchor-photo/20260506/9cc66e63cd7442e193cf35579586a077.jpg",
        "onlineCount": 27706
    },
    {
        "anchorId": "1801235281629249538",
        "anchorNickname": "🦜SC-1M5D",
        "liveName": "🦜SC-1M5D",
        "coverUrl": "https://img.y3cdn.com/20260515/7ef332dfe2d344618a072fbd861061bc.jpg",
        "onlineCount": 4600
    },
    {
        "anchorId": "2034362658249592834",
        "anchorNickname": "[🐉] LinkHayCuoi🦖",
        "liveName": "LinkHayCuoi live ạ🦖",
        "coverUrl": "https://img.y3cdn.com/20260708/8171fbb8f1e549b5a027c817845470da.jpg",
        "onlineCount": 2737
    },
    {
        "anchorId": "1742159502648680450",
        "anchorNickname": "💙Munlove🍑",
        "liveName": "Idol mới 🥰",
        "coverUrl": "https://img.y3cdn.com/20240102/4bbb1fa1534e4298acac9c3594ab885b.jpg",
        "onlineCount": 115663
    }
]

@app.route('/')
def index():
    # Nhớ đổi tên file giao diện của bạn thành index1.html hoặc index.html cho khớp ở đây
    return render_template('index1.html')

@app.route('/api/idols')
def get_idols():
    """Trả về danh sách dữ liệu cứng từ file traffic log"""
    return jsonify({
        "code": 200,
        "data": {
            "records": MOCK_IDOLS
        }
    })

@app.route('/api/room-info', methods=['POST'])
def get_room_info():
    """Trả về link stream live hoạt động được dựa trên ID của từng Idol"""
    data = request.json
    anchor_id = data.get("anchorId")
    
    # Do các link m3u8 trong log cũ đã hết hạn (403), chúng ta map các luồng HLS test trực tiếp 
    # đang hoạt động mượt mà để kiểm tra tính năng "chọn Idol nào stream Idol đó".
    stream_urls = {
        "1787831695819378689": "https://playertest.longtailvideo.com/adaptive/bigbuckbunny/bigbuckbunny.m3u8",
        "1872165888673222657": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        "1900493761044578305": "https://playertest.longtailvideo.com/adaptive/pip/manifest.m3u8",
        "1598549452099416065": "https://samples.vod.video.ibm.com/hls/index.m3u8",
        "1801235281629249538": "https://playertest.longtailvideo.com/adaptive/bigbuckbunny/bigbuckbunny.m3u8",
        "2034362658249592834": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        "1742159502648680450": "https://samples.vod.video.ibm.com/hls/index.m3u8"
    }
    
    # Lấy luồng tương ứng hoặc dùng luồng mặc định nếu không khớp
    chosen_url = stream_urls.get(anchor_id, "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8")

    return jsonify({
        "code": 200,
        "data": {
            "pullAddr": chosen_url,
            "tblCnts": ["Hát", "Nhảy", "Thay quần áo", "Tương tác"]
        }
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)