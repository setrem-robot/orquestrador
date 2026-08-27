"""Os cabeçalhos de segurança saem em toda resposta — inclusive nos erros.

Um header que só aparece no 200 não protege nada: o navegador precisa dele
também na resposta que ele de fato renderiza quando algo dá errado.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import _CABECALHOS_SEGURANCA, app


class TestCabecalhosDeSeguranca(unittest.TestCase):
    def setUp(self) -> None:
        self.cliente = TestClient(app)

    def test_saude_traz_todos_os_cabecalhos(self) -> None:
        r = self.cliente.get("/saude")
        for chave, valor in _CABECALHOS_SEGURANCA.items():
            self.assertEqual(r.headers.get(chave), valor, chave)

    def test_401_tambem_traz_os_cabecalhos(self) -> None:
        # A rota do app sem token responde 401 — e o navegador vê essa resposta,
        # então ela precisa dos mesmos cabeçalhos.
        r = self.cliente.get("/v1/estado")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("Content-Security-Policy"),
                         "default-src 'none'; frame-ancestors 'none'")

    def test_nosniff_e_hsts_presentes(self) -> None:
        r = self.cliente.get("/saude")
        self.assertIn("max-age", r.headers.get("Strict-Transport-Security", ""))
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")


if __name__ == "__main__":
    unittest.main()
