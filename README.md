# Long Text Chatterbox

**Long Text Chatterbox** recibe un texto largo, lo limpia y lo fragmenta en trozos, envía cada trozo al servicio **Chatterbox** dockerizado para generar los audios parciales y finalmente los une en un único MP3.

Es el equivalente a `long_text_coqui`, pero sustituye el servicio de Coqui XTTS por Chatterbox.

## Características

- Convierte textos largos a voz.
- Fragmenta automáticamente el texto por puntuación.
- Limpia emojis/caracteres especiales y convierte números a palabras en español.
- Permite forzar efectos y silencios con etiquetas `<silence1>`, `<click2>` y `<clicl1>`.
- Utiliza Docker para facilitar la implementación y ejecución.

## Servicios externos de dependencia

Esta aplicación requiere que el servicio de Chatterbox esté corriendo y accesible. La URL se configura con la variable de entorno `SERVER_URL_CHATTERBOX` (por defecto `http://192.168.1.69:8020`).

Chatterbox se invoca a través de su API de Gradio:

```bash
# 1. Solicitar generación
curl -s -X POST "http://192.168.1.69:8020/gradio_api/call/generate_tts_audio" \
  -H "Content-Type: application/json" \
  -d '{
    "data": [
      "Hola, estamos probando Chatterbox en español de España.",
      null,
      0.5,
      0.8,
      0,
      0.5
    ]
  }'

# 2. Esperar el evento y descargar el audio
curl -sN "http://192.168.1.69:8020/gradio_api/call/generate_tts_audio/<EVENT_ID>"
```

Orden de los parámetros de `data`:

1. Texto a sintetizar.
2. Audio de referencia opcional (`null` para usar el de la interfaz).
3. `exaggeration`.
4. `temperature`.
5. `seed` (`0` para aleatoria).
6. `cfg_weight`.

> **Nota:** el ejemplo anterior refleja el formato del endpoint. En la versión actual de Chatterbox, enviar `null` como audio de referencia directamente al endpoint puede devolver `event: error`. Esta aplicación resuelve automáticamente el audio de referencia por defecto de la interfaz Gradio (`/config`) cuando `reference_audio` es `null`. Si se indica `reference_audio`, se puede pasar una ruta del servidor, una URL de Gradio o el objeto `FileData` completo.

## Endpoints disponibles

La API corre por defecto en el puerto `5009`.

### 1. **POST /text-to-speech** - Conversión asincrónica

Convierte texto a voz en forma asincrónica, dividiendo textos largos automáticamente.

**Body:**

```json
{
  "text": "Tu texto aquí",
  "reference_audio": null,
  "exaggeration": 0.5,
  "temperature": 0.8,
  "seed": 0,
  "cfg_weight": 0.5
}
```

**Respuesta:**

```json
{
  "job_id": "uuid-del-trabajo",
  "status": "queued",
  "audio_url": "/status/uuid-del-trabajo"
}
```

### 2. **GET /status/{job_id}** - Estado del trabajo

Obtiene el estado actual de una tarea de síntesis.

### 3. **GET /audio/{job_id}** - Descargar audio

Descarga el archivo MP3 generado (disponible cuando el estado es `completed`).

### 4. **POST /tts_stream** - Streaming directo

Convierte texto a voz con respuesta en tiempo real (streaming WAV), sin fragmentar.

**Body:**

```json
{
  "text": "Tu texto aquí",
  "reference_audio": null,
  "exaggeration": 0.5,
  "temperature": 0.8,
  "seed": 0,
  "cfg_weight": 0.5
}
```

**Respuesta:** Stream de audio WAV

## Ejecutar con Docker

1. **Correr el contenedor Docker:**

   ```bash
   docker run -d \
     --name long_text_chatterbox \
     -v chatterbox_data:/data \
     -p 5009:5009 \
     -e SERVER_URL_CHATTERBOX=http://192.168.1.69:8020 \
     manologcode/long_text_chatterbox
   ```

   Asegúrate de reemplazar `http://192.168.1.69:8020` con la URL o nombre del servicio de tu Chatterbox.

   Los audios generados y los temporales se guardan en el volumen `chatterbox_data` (`/data/audio_files` y `/data/temp_audio`). Así se evitan problemas de permisos cuando el código se monta desde el host con `-v ./app:/app`.

2. **Probar la conversión de texto a voz:**

   ```bash
   docker exec long_text_chatterbox python test.py "esto es un texto de prueba para que lo leas"
   ```

   Esto generará un audio con el texto proporcionado.

## Ejecutar con Docker Compose

### Desde Docker Hub (sin construir la imagen)

Descarga la imagen publicada `manologcode/long_text_chatterbox` y levanta el contenedor `long_text_chatterbox`:

```bash
docker compose pull
docker compose up -d --no-build
```

La imagen utilizada es `manologcode/long_text_chatterbox:latest` (definida en `docker-compose.yml`).

### Construyendo la imagen en local

```bash
docker compose up -d --build
```

## Ejecutar en local sin Docker

```bash
cd app
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 5009
```

## Etiquetas de efectos

Se pueden forzar efectos y silencios añadiendo las etiquetas `<silence1>`, `<click2>` y `<clicl1>` dentro del texto. Los archivos de audio correspondientes están en `app/effects/`.
