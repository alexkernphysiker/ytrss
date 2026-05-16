from flask import Flask
from utils import *
from config import *

host = get_config()["host_public"]
port=get_config()["port_public"]
url_link=f"http://{host}:{port}"
print(f"Using IP: {host} for public access. Make sure this IP is correct and accessible from the outside.")

app = Flask(__name__)
@app.route("/feed")
def yt_feed():
    global url_link
    return generate_feed(url_link, True)

@app.route("/file/<path:filename>.mp4")
def download(filename):
    return return_file(filename)

if __name__ == "__main__":
    app.run(host=get_local_ip(), port=port)