from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse as up

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/run'):
            q = up.parse_qs(up.urlparse(self.path).query).get('q',[''])[0]
            out = f"入力された文字：{q}\n文字数：{len(q)}文字"  # 文字数を数える
            self._ok(f"<pre>{out}</pre><p><a href='/'>戻る</a></p>")
        else:
            with open('ui.html','rb') as f: self._ok(f.read())

    def _ok(self, b):
        if isinstance(b,str): b=b.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b)

if __name__=='__main__':
    print("サーバー起動中... http://127.0.0.1:8000/ を開いてください")
    print("ネットワーク共有: 同じネットワーク内の他デバイスからもアクセス可能")
    HTTPServer(('0.0.0.0',8000),H).serve_forever()