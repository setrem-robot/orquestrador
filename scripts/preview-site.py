"""Prévia local do site e painel, com proxy limitado à API pública da Atlas."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import argparse

SITE = Path(__file__).resolve().parents[1] / "site"
ORIGEM = "https://atlas.kerlonr.com.br"
PUBLICAS = {"/api/v1/publico/resumo", "/api/v1/publico/trajeto", "/api/v1/publico/saude"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def do_GET(self):
        caminho = urlsplit(self.path).path
        if caminho.startswith("/api/"):
            if caminho not in PUBLICAS:
                self.send_error(404)
                return
            try:
                pedido = Request(ORIGEM + self.path, headers={
                    "Accept": "application/json", "User-Agent": "AtlasPreview/1.0",
                })
                with urlopen(pedido, timeout=10) as resposta:
                    corpo = resposta.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
            except HTTPError as exc:
                self.send_error(exc.code, "Consulta pública indisponível")
            except (URLError, TimeoutError):
                self.send_error(502, "Não foi possível alcançar a API pública")
            return
        super().do_GET()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8093)
    args = parser.parse_args()
    servidor = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Atlas: http://127.0.0.1:{args.port}/", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()
