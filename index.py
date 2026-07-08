from flask import Flask, render_template

from platforms.mmlive_api import mmlive_bp
from platforms.stripchat_api import stripchat_bp
from platforms.yylive_api import yylive_bp

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


app.register_blueprint(mmlive_bp)
app.register_blueprint(yylive_bp)
app.register_blueprint(stripchat_bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
