# Despliegue en el NAS

`docker-compose.yml` y `.env` en `/volume1/docker/energia-monitor`. Cambiar el
`.env` exige `docker compose up -d` (un `restart` no relee las variables).

## Formulario de batería (servicio `formulario`)

Página HTTPS para el móvil: https://rdv-nas.tail1aac93.ts.net:8421/

- **Solo dentro del tailnet; nunca detrás de Tailscale Funnel.** No tiene
  usuario ni contraseña: cualquiera que llegue al puerto puede anotar lecturas.
  El puerto se publica solo en la IP del NAS en el tailnet (`FORMULARIO_BIND`)
  y conviene limitarlo con una ACL (abajo).
- Solo responde a `FORMULARIO_HOST` (otro `Host`: 421) y solo acepta POST del
  mismo origen. La página no consulta el cargador.
- Rol de PostgreSQL `formulario_rw`; su contraseña (`FORMULARIO_DB_PASSWORD`)
  la fija el colector al arrancar.

Variables de `.env` (obligatorias para `docker compose up`):

```sh
FORMULARIO_DB_PASSWORD=...
FORMULARIO_BIND=100.102.118.77
FORMULARIO_HOST=rdv-nas.tail1aac93.ts.net:8421
FORMULARIO_GRAFANA_URL=          # opcional
```

### Certificado: primera copia

El contenedor lee `server.crt` y `server.key` de
`/volume1/docker/energia-monitor/certs` (montado en solo lectura en `/certs`) y
corre como el usuario `app` de la imagen, UID/GID **10001**. El directorio está
en `.gitignore`.

Primera vez, como root en el NAS, con el certificado y la clave del nombre del
tailnet (los que genera `tailscale cert rdv-nas.tail1aac93.ts.net` o el script
`/volume1/scripts/renew-ts-cert.sh`):

```sh
cd /volume1/docker/energia-monitor
mkdir -p certs
cp <origen>/rdv-nas.tail1aac93.ts.net.crt certs/server.crt
cp <origen>/rdv-nas.tail1aac93.ts.net.key certs/server.key
chown -R 10001:10001 certs
chmod 0750 certs
chmod 0444 certs/server.crt
chmod 0400 certs/server.key
```

La clave solo la puede leer el UID 10001. Si el servicio no la puede leer, no
arranca (`formulario sin arrancar: no se puede cargar ...` en
`docker compose logs formulario`).

### Renovación

El script de renovación (fuera del repositorio) copia los ficheros nuevos con:

```sh
deploy_ct /volume1/docker/energia-monitor/certs energia-formulario
```

`energia-formulario` es el `container_name` fijo del servicio. El servidor
recarga el certificado en cuanto cambian los ficheros (no hace falta reiniciar;
si `deploy_ct` reinicia el contenedor, también vale). La copia debe conservar el
dueño 10001 y los permisos de arriba: si el certificado nuevo no se puede leer,
el servidor sigue con el anterior y lo deja en el log.

Comprobación tras renovar:

```sh
docker compose exec formulario python -m energia_monitor formulario salud
docker inspect --format '{{.State.Health.Status}}' energia-formulario
```

### Healthcheck

`formulario salud` pide `https://127.0.0.1:8443/salud` validando el certificado
con el nombre del tailnet y las CA del sistema. El servicio queda **no sano**
si el certificado está caducado, es de otro nombre o falta (en este caso el
servidor no arranca), si la base de datos no responde o si falta la migración
que espera la imagen. `/salud` no comprueba el `Host` y devuelve JSON con la
versión, la migración, la antigüedad de la última muestra y el último recálculo.

### Arranque del NAS: Docker antes que Tailscale

El puerto se publica en la IP del tailnet. Si al arrancar el NAS Docker levanta
el contenedor antes de que exista la interfaz `tailscale0`, la publicación falla
(«cannot assign requested address») y Docker **no lo reintenta**. Dos remedios
(basta uno):

1. **Tarea de arranque de DSM** (Panel de control → Programador de tareas →
   Crear → Tarea activada → Script definido por el usuario; evento: arranque;
   usuario: root):

   ```sh
   #!/bin/sh
   # Espera a que Tailscale tenga su IP y levanta el stack.
   for i in $(seq 1 60); do
       ip -4 addr show tailscale0 2>/dev/null | grep -q '100.102.118.77' && break
       sleep 5
   done
   cd /volume1/docker/energia-monitor && docker compose up -d
   ```

2. **Permitir enlazar una IP que aún no existe** (misma tarea de arranque, antes
   de que arranque Docker si es posible):

   ```sh
   sysctl -w net.ipv4.ip_nonlocal_bind=1
   ```

   Afecta a todo el NAS; las actualizaciones de DSM pueden no conservar
   `/etc/sysctl.conf`, por eso va en la tarea de arranque.

Tras reiniciar el NAS: `docker compose ps formulario` debe mostrar `healthy`.

### Limitar el puerto 8421 a tus dispositivos (ACL de Tailscale)

En la consola de administración de Tailscale (Access controls), la política
(HuJSON) decide qué dispositivos llegan a qué puertos. Las ACL son una lista de
permisos: si la política aún tiene la regla por defecto que lo permite todo
(`"src": ["*"], "dst": ["*:*"]`), hay que sustituirla, porque si no el resto de
reglas no limita nada. Ejemplo, con tu usuario del tailnet en lugar de
`tu-usuario@ejemplo.com`:

```jsonc
{
  "hosts": {
    "rdv-nas": "100.102.118.77",
  },
  "acls": [
    // Formulario de batería: solo tus dispositivos.
    {"action": "accept", "src": ["tu-usuario@ejemplo.com"], "dst": ["rdv-nas:8421"]},
    // El resto de lo que ya usas del NAS y de los demás equipos: revísalo antes de
    // quitar la regla por defecto (Gitea en 8418, Grafana, SSH...).
    {"action": "accept", "src": ["tu-usuario@ejemplo.com"], "dst": ["*:*"]},
  ],
  // La consola rechaza la política si estas comprobaciones fallan.
  "tests": [
    {"src": "tu-usuario@ejemplo.com", "accept": ["rdv-nas:8421"]},
    {"src": "otro-usuario@ejemplo.com", "deny": ["rdv-nas:8421"]},
  ],
}
```

Para limitarlo a dispositivos concretos y no a todos los de tu usuario, etiqueta
esos dispositivos (`"tagOwners"` y `tag:mios`) y usa `"src": ["tag:mios"]`.
Comprobación: desde un dispositivo no permitido,
`curl -m 5 https://rdv-nas.tail1aac93.ts.net:8421/salud` no debe conectar. Esta
sección no está verificada en tu tailnet: revisa la política con la vista
previa de la consola antes de guardarla.
