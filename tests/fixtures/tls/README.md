# Certificados TLS de pruebas

Solo para los tests del formulario (tests/test_formulario_tls.py). No son
secretos ni sirven fuera de los tests: la CA (`ca.crt`) es de pruebas y su
clave no se guardó.

- `servidor.key`: clave de los tres certificados.
- `valido.crt`: `formulario.test`, válido de 2026 a 2125.
- `caducado.crt`: `formulario.test`, caducado el 1/1/2021.
- `otro_nombre.crt`: `otro.test`, válido.

Generados el 15/11/2026 con OpenSSL 3.5 (`openssl x509 -req -not_before/-not_after`).
