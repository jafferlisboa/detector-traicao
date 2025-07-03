from flask import Flask, send_file, abort, render_template
import os
import qrcode
import io

app = Flask(__name__)

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/qr/<session_id>')
def mostrar_qr(session_id):
    path = f"./qrcodes/{session_id}.txt"
    if not os.path.exists(path):
        return abort(404, "QR Code ainda não foi gerado.")

    with open(path, "r") as f:
        qr_data = f.read()

    img = qrcode.make(qr_data)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype="image/png")
