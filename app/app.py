from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import json
import uuid
import re
from urllib.parse import unquote
from pydub import AudioSegment
import logging
from num2words import num2words

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Text-to-Speech API (Chatterbox)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite todas las origins en desarrollo. En producción, limita esto.
    allow_credentials=True,
    allow_methods=["*"],  # Permite todos los métodos
    allow_headers=["*"],  # Permite todos los headers
)

class TextToSpeechRequest(BaseModel):
    text: str
    reference_audio: str | dict | None = None
    exaggeration: float = 0.5
    temperature: float = 0.8
    seed: int = 0
    cfg_weight: float = 0.5

class TextToSpeechResponse(BaseModel):
    audio_url: str
    job_id: str
    status: str

class TTSStreamRequest(BaseModel):
    text: str
    reference_audio: str | dict | None = None
    exaggeration: float = 0.5
    temperature: float = 0.8
    seed: int = 0
    cfg_weight: float = 0.5

class Config:
    AUDIO_FILES_DIR = os.getenv("AUDIO_FILES_DIR", "audio_files")
    TEMP_DIR = os.getenv("TEMP_DIR", "temp_audio")
    AUDIO_TAGS_DIR = os.getenv("AUDIO_TAGS_DIR", "effects")
    SERVER_URL = os.getenv("SERVER_URL_CHATTERBOX", "http://192.168.1.69:8020")
    GRADIO_CALL_ENDPOINT = "/gradio_api/call/generate_tts_audio"
    MAX_CHUNK_SIZE = 230

# Crear directorios si no existen
os.makedirs(Config.AUDIO_FILES_DIR, exist_ok=True)
os.makedirs(Config.TEMP_DIR, exist_ok=True)
os.makedirs(Config.AUDIO_TAGS_DIR, exist_ok=True)

tasks_status = {}

def clean_spanish_text(text: str) -> str:
    """
    Limpia el texto eliminando emojis, iconos y caracteres especiales,
    preservando acentos y la ñ propios del español.

    Args:
        text (str): Texto original que puede contener emojis e iconos

    Returns:
        str: Texto limpio manteniendo acentos y ñ, sin caracteres extraños
    """
    if not text:
        return ""

    # Definir caracteres válidos para español (incluyendo acentos y ñ)
    # Incluye letras acentuadas, ñ/Ñ, números, puntuación común y espacios
    valid_chars_pattern = r'[a-zA-ZáéíóúüÁÉÍÓÚÜñÑ0-9\s\.,;:!?¿¡\-_"\'()\[\]{}\n\r\t]'

    # Filtrar solo los caracteres válidos
    cleaned_chars = []
    for char in text:
        if re.match(valid_chars_pattern, char):
            cleaned_chars.append(char)
        # Reemplazar algunos caracteres especiales comunes por equivalentes
        elif char in '""''':
            cleaned_chars.append('"')
        elif char in '–—':
            cleaned_chars.append('-')
        elif char == '…':
            cleaned_chars.append('...')
        # Ignorar otros caracteres (emojis, iconos, etc.)

    cleaned_text = ''.join(cleaned_chars)

    # Limpiar espacios múltiples y saltos de línea excesivos
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text)  # Múltiples espacios -> un espacio
    cleaned_text = re.sub(r'\n+', '\n', cleaned_text)  # Múltiples saltos -> un salto
    cleaned_text = cleaned_text.strip()

    # Limpiar puntuación duplicada
    cleaned_text = re.sub(r'([.,;:!?]){2,}', r'\1', cleaned_text)

    # Asegurar que las oraciones tengan estructura básica válida
    # Eliminar puntuación al inicio de palabras (excepto signos de apertura)
    cleaned_text = re.sub(r'\b[.,;:!?]+([a-zA-ZáéíóúüñÁÉÍÓÚÜÑ])', r' \1', cleaned_text)

    # Limpiar espacios alrededor de puntuación
    cleaned_text = re.sub(r'\s+([.,;:!?])', r'\1', cleaned_text)
    cleaned_text = re.sub(r'([¿¡])\s+', r'\1', cleaned_text)

    return cleaned_text.strip()

def number_to_text_spanish(number: int) -> str:
    """
    Convierte un número entero a su representación en texto en español usando num2words.

    Args:
        number (int): Número entero a convertir

    Returns:
        str: Representación en texto del número en español
    """
    try:
        return num2words(number, lang='es')
    except Exception as e:
        # Si hay algún error, devolver el número como string
        logger.warning(f"Error al convertir número {number} a texto: {e}")
        return str(number)

def convert_numbers_to_text(text: str) -> str:
    """
    Convierte todos los números encontrados en el texto a su representación en español.
    Maneja números con separadores de miles (comas y puntos) y números decimales.
    Evita convertir códigos, versiones, IPs y otros patrones que no son números hablados.

    Args:
        text (str): Texto que puede contener números

    Returns:
        str: Texto con los números convertidos a palabras en español
    """
    def should_convert_number(match, text, start_pos, end_pos):
        """Determina si un número debe convertirse basándose en el contexto"""
        number_str = match.group(0)

        # Obtener contexto antes y después del número
        context_before = text[max(0, start_pos-10):start_pos].lower()
        context_after = text[end_pos:min(len(text), end_pos+10)].lower()

        # Patrones que indican que NO se debe convertir
        skip_patterns = [
            # Versiones de software
            r'versi[oó]n\s*$',
            r'v\d*\.?\s*$',

            # Códigos
            r'c[oó]digo\s*:?\s*$',
            r'ref\s*:?\s*$',
            r'id\s*:?\s*$',

            # Direcciones IP y URLs
            r'ip\s*:?\s*$',
            r'http[s]?\s*:?\s*$',

            # Fechas
            r'fecha\s*:?\s*$',

            # Teléfonos
            r'tel[eé]fono\s*:?\s*$',
            r'tel\s*:?\s*$',
            r'phone\s*:?\s*$',
        ]

        # Verificar patrones antes del número
        for pattern in skip_patterns:
            if re.search(pattern, context_before):
                return False

        # Patrones después que indican códigos o versiones
        after_patterns = [
            r'^\.\d+',  # Más números después de punto (versiones, IPs)
            r'^[a-zA-Z]',  # Letras inmediatamente después
        ]

        for pattern in after_patterns:
            if re.search(pattern, context_after):
                return False

        # Si tiene muchos puntos separados, probablemente es un código
        if number_str.count('.') > 2:
            return False

        return True

    def replace_number(match):
        number_str = match.group(0)
        start_pos = match.start()
        end_pos = match.end()

        # Verificar si debemos convertir este número
        if not should_convert_number(match, text, start_pos, end_pos):
            return number_str

        try:
            # Caso 1: Números con coma como separador de miles y punto decimal (ej: 1,234.56)
            if ',' in number_str and '.' in number_str:
                # Verificar patrón: dígitos,dígitos,dígitos.dígitos
                if re.match(r'^\d{1,3}(?:,\d{3})+\.\d{1,2}$', number_str):
                    # Separar parte entera y decimal
                    integer_part, decimal_part = number_str.split('.')
                    clean_integer = integer_part.replace(',', '')

                    integer_num = int(clean_integer)
                    decimal_num = int(decimal_part)

                    result = number_to_text_spanish(integer_num)
                    if decimal_num > 0:
                        result += f" coma {number_to_text_spanish(decimal_num)}"
                    return result

            # Caso 2: Números con coma como separador de miles (ej: 1,000 o 1,000,000)
            elif ',' in number_str and '.' not in number_str:
                # Verificar que sea un patrón válido de separador de miles
                if re.match(r'^\d{1,3}(?:,\d{3})+$', number_str):
                    clean_number = number_str.replace(',', '')
                    number = int(clean_number)
                    return number_to_text_spanish(number)

            # Caso 3: Números con punto como separador de miles (ej: 1.000 o 1.000.000)
            elif '.' in number_str:
                # Verificar si es separador de miles (múltiples grupos de 3 dígitos)
                if re.match(r'^\d{1,3}(?:\.\d{3})+$', number_str):
                    clean_number = number_str.replace('.', '')
                    number = int(clean_number)
                    return number_to_text_spanish(number)
                # Verificar si es número decimal (solo un punto con 1-2 dígitos después)
                elif re.match(r'^\d+\.\d{1,2}$', number_str):
                    number = float(number_str)
                    integer_part = int(number)
                    decimal_part = int(round((number - integer_part) * 100))

                    result = number_to_text_spanish(integer_part)
                    if decimal_part > 0:
                        result += f" coma {number_to_text_spanish(decimal_part)}"
                    return result

            # Caso 4: Números enteros simples
            elif number_str.isdigit():
                number = int(number_str)
                return number_to_text_spanish(number)

            # Si no coincide con ningún patrón conocido, devolver original
            return number_str

        except (ValueError, OverflowError):
            # Si no se puede convertir, devolver el número original
            logger.warning(f"No se pudo convertir el número: {number_str}")
            return number_str

    # Patrón mejorado para encontrar diferentes tipos de números en orden de prioridad:
    patterns = [
        r'\b\d{1,3}(?:,\d{3})+\.\d{1,2}\b',       # Con comas y decimales
        r'\b\d{1,3}(?:,\d{3})+\b',                # Con comas como separadores de miles
        r'\b\d{1,3}(?:\.\d{3})+\b',               # Con puntos como separadores de miles
        r'\b\d+\.\d{1,2}\b',                      # Decimales simples
        r'\b\d+\b'                                # Números enteros simples
    ]

    # Aplicar cada patrón en orden de prioridad
    result_text = text
    for pattern in patterns:
        result_text = re.sub(pattern, replace_number, result_text)

    return result_text

def extract_tags_and_clean_text(text: str):
    """
    Extrae las etiquetas del texto y devuelve una lista de elementos ordenados.
    Cada elemento es un diccionario con 'type' ('text' o 'tag') y 'content'.
    Además, limpia el texto de emojis e iconos.
    """
    # Primero limpiar el texto de emojis e iconos, pero preservar las etiquetas
    # Patrón para encontrar etiquetas como <click2>, <silence1>, etc.
    tag_pattern = r'<([^>]+)>'

    # Extraer etiquetas temporalmente para preservarlas durante la limpieza
    tags_found = []
    tag_placeholders = []

    for i, match in enumerate(re.finditer(tag_pattern, text)):
        tag_placeholder = f"__TAG_PLACEHOLDER_{i}__"
        tags_found.append(match.group(0))  # Etiqueta completa con < >
        tag_placeholders.append(tag_placeholder)

    # Reemplazar etiquetas con placeholders temporales
    text_with_placeholders = text
    for i, tag in enumerate(tags_found):
        text_with_placeholders = text_with_placeholders.replace(tag, tag_placeholders[i], 1)

    # Limpiar el texto (sin las etiquetas)
    cleaned_text = clean_spanish_text(text_with_placeholders)

    # Convertir números a texto
    cleaned_text = convert_numbers_to_text(cleaned_text)

    # Restaurar las etiquetas
    for i, placeholder in enumerate(tag_placeholders):
        cleaned_text = cleaned_text.replace(placeholder, tags_found[i])

    # Ahora procesar el texto limpio para extraer elementos
    elements = []
    last_end = 0

    for match in re.finditer(tag_pattern, cleaned_text):
        # Agregar texto antes de la etiqueta
        if match.start() > last_end:
            text_content = cleaned_text[last_end:match.start()].strip()
            if text_content:
                elements.append({'type': 'text', 'content': text_content})

        # Agregar la etiqueta
        tag_name = match.group(1)
        elements.append({'type': 'tag', 'content': tag_name})

        last_end = match.end()

    # Agregar texto restante después de la última etiqueta
    if last_end < len(cleaned_text):
        text_content = cleaned_text[last_end:].strip()
        if text_content:
            elements.append({'type': 'text', 'content': text_content})

    return elements

def split_text_by_punctuation(text: str, max_length: int = Config.MAX_CHUNK_SIZE):
    """Divide el texto por puntuación manteniendo el tamaño máximo."""
    signos_puntuacion = re.compile(r'([.,;:!?])')
    palabras = signos_puntuacion.split(text)
    trozos = []
    actual = ""

    for i in range(0, len(palabras), 2):
        segmento = palabras[i] + (palabras[i+1] if i+1 < len(palabras) else "")

        if len(actual) + len(segmento) <= max_length:
            actual += segmento
        else:
            if actual:
                trozos.append(actual.strip())
            actual = segmento

    if actual:
        trozos.append(actual.strip())

    return trozos

def make_gradio_filedata(path: str, url: str | None = None):
    """Construye la estructura FileData que espera Gradio para un audio de referencia."""
    path = unquote(path)
    if not url:
        url = f"{Config.SERVER_URL}/gradio_api/file={path}"
    return {
        "path": path,
        "url": url,
        "size": None,
        "orig_name": os.path.basename(path),
        "mime_type": None,
        "is_stream": False,
        "meta": {"_type": "gradio.FileData"},
    }

def get_default_reference_audio():
    """
    Obtiene el audio de referencia por defecto de la interfaz Gradio de Chatterbox.
    Chatterbox no genera audio si se envía null como referencia.
    """
    try:
        response = requests.get(f"{Config.SERVER_URL}/config", timeout=15)
        response.raise_for_status()
        config = response.json()
        for component in config.get("components", []):
            props = component.get("props", {}) or {}
            label = (props.get("label") or "").lower()
            if component.get("type") == "audio" and "reference" in label:
                value = props.get("value")
                if value:
                    return value
    except Exception as e:
        logger.warning(f"No se pudo obtener el audio de referencia por defecto: {e}")
    return None

def resolve_reference_audio(reference_audio):
    """Normaliza el audio de referencia recibido a un FileData de Gradio."""
    if reference_audio is None:
        return get_default_reference_audio()
    if isinstance(reference_audio, dict):
        return reference_audio
    if isinstance(reference_audio, str):
        value = reference_audio.strip()
        if not value:
            return get_default_reference_audio()
        marker = "/gradio_api/file="
        if marker in value:
            path = value.split(marker, 1)[1]
            return make_gradio_filedata(path, value)
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return make_gradio_filedata(value)
    return None

def extract_audio_url(data):
    """Extrae la URL del audio de la respuesta SSE de Gradio."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if url:
                return url
            path = item.get("path")
            if path:
                return f"{Config.SERVER_URL}/gradio_api/file={path}"
        elif isinstance(item, str) and item.startswith(("http://", "https://")):
            return item
    return None

def download_audio(url: str, output_filename: str):
    """Descarga el audio generado por Chatterbox."""
    if url.startswith("/"):
        url = f"{Config.SERVER_URL}{url}"
    with requests.get(url, stream=True, timeout=(10, 300)) as response:
        response.raise_for_status()
        with open(output_filename, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    return output_filename

def generate_chatterbox_audio(
    text_chunk: str,
    reference_audio,
    exaggeration: float,
    temperature: float,
    seed: int,
    cfg_weight: float,
    output_filename: str,
):
    """Genera un audio llamando al endpoint de Gradio de Chatterbox."""
    payload = {
        "data": [
            text_chunk,
            reference_audio,
            exaggeration,
            temperature,
            seed,
            cfg_weight,
        ]
    }

    response = requests.post(
        f"{Config.SERVER_URL}{Config.GRADIO_CALL_ENDPOINT}",
        json=payload,
        timeout=30,
    )
    if response.status_code != 200:
        raise Exception(f"Error al solicitar la generación: {response.text}")

    event_id = response.json().get("event_id")
    if not event_id:
        raise Exception(f"La API no devolvió event_id: {response.text}")

    with requests.get(
        f"{Config.SERVER_URL}{Config.GRADIO_CALL_ENDPOINT}/{event_id}",
        stream=True,
        timeout=(10, 600),
    ) as event_response:
        if event_response.status_code != 200:
            raise Exception(f"Error al esperar el audio: {event_response.text}")

        for raw_line in event_response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str or data_str == "null":
                continue
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("error"):
                raise Exception(f"Error de Chatterbox: {data['error']}")
            audio_url = extract_audio_url(data)
            if audio_url:
                return download_audio(audio_url, output_filename)

    raise Exception("Chatterbox no devolvió ningún audio")

def process_text_chunk(
    text_chunk: str,
    reference_audio,
    exaggeration: float,
    temperature: float,
    seed: int,
    cfg_weight: float,
    chunk_id: int,
    job_id: str,
):
    """Procesa un fragmento de texto y lo convierte en audio con Chatterbox."""
    try:
        chunk_filename = f"{Config.TEMP_DIR}/{job_id}_{chunk_id}.wav"
        return generate_chatterbox_audio(
            text_chunk,
            reference_audio,
            exaggeration,
            temperature,
            seed,
            cfg_weight,
            chunk_filename,
        )
    except Exception as e:
        logger.error(f"Error en fragmento {chunk_id}: {str(e)}")
        tasks_status[job_id]["errors"].append(f"Error en fragmento {chunk_id}: {str(e)}")
        return None

def get_tag_audio_file(tag_name: str):
    """Obtiene la ruta del archivo de audio para una etiqueta específica."""
    tag_file = os.path.join(Config.AUDIO_TAGS_DIR, f"{tag_name}.mp3")
    if os.path.exists(tag_file):
        return tag_file
    else:
        logger.warning(f"Archivo de etiqueta no encontrado: {tag_file}")
        return None

def merge_audio_elements(audio_files: list, output_filename: str):
    """Une múltiples archivos de audio en uno solo."""
    try:
        if not audio_files:
            raise Exception("No hay archivos de audio para unir")

        # Filtrar archivos que no existen
        existing_files = [f for f in audio_files if f and os.path.exists(f)]

        if not existing_files:
            raise Exception("No se encontraron archivos de audio válidos")

        combined = AudioSegment.from_file(existing_files[0])
        for archivo in existing_files[1:]:
            sound = AudioSegment.from_file(archivo)
            combined += sound

        combined.export(output_filename, format="mp3", bitrate="256k")
        return output_filename
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al unir archivos: {str(e)}")

async def process_text_to_speech(
    text: str,
    reference_audio_source,
    exaggeration: float,
    temperature: float,
    seed: int,
    cfg_weight: float,
    job_id: str,
):
    """Procesa el texto dividiéndolo si es necesario y manejando las etiquetas."""
    try:
        tasks_status[job_id]["status"] = "processing"

        reference_audio = resolve_reference_audio(reference_audio_source)

        # Extraer etiquetas y limpiar texto
        elements = extract_tags_and_clean_text(text)

        if not elements:
            raise Exception("No hay contenido para procesar")

        audio_files = []
        text_chunk_counter = 0

        for element in elements:
            if element['type'] == 'text':
                # Procesar texto
                text_content = element['content']

                if len(text_content) > Config.MAX_CHUNK_SIZE:
                    # Dividir texto largo en chunks
                    chunks = split_text_by_punctuation(text_content)
                    for chunk in chunks:
                        if chunk.strip():
                            audio_file = process_text_chunk(
                                chunk, reference_audio, exaggeration, temperature, seed,
                                cfg_weight, text_chunk_counter, job_id,
                            )
                            if audio_file:
                                audio_files.append(audio_file)
                            text_chunk_counter += 1
                else:
                    # Procesar como un solo chunk
                    if text_content.strip():
                        audio_file = process_text_chunk(
                            text_content, reference_audio, exaggeration, temperature, seed,
                            cfg_weight, text_chunk_counter, job_id,
                        )
                        if audio_file:
                            audio_files.append(audio_file)
                        text_chunk_counter += 1

            elif element['type'] == 'tag':
                # Procesar etiqueta
                tag_name = element['content']
                tag_audio_file = get_tag_audio_file(tag_name)
                if tag_audio_file:
                    audio_files.append(tag_audio_file)
                    logger.info(f"Agregado archivo de etiqueta: {tag_name}")
                else:
                    logger.warning(f"Archivo de etiqueta no encontrado: {tag_name}")

        if not audio_files:
            raise Exception("No se generaron archivos de audio")

        output_filename = f"{Config.AUDIO_FILES_DIR}/{job_id}_complete.mp3"
        merge_audio_elements(audio_files, output_filename)

        tasks_status[job_id].update({
            "status": "completed",
            "audio_url": f"/audio/{job_id}",
            "output_file": output_filename
        })

        # Limpiar archivos temporales (solo los generados, no los de etiquetas)
        for file in audio_files:
            if file and file.startswith(Config.TEMP_DIR) and os.path.exists(file):
                os.remove(file)

    except Exception as e:
        logger.error(f"Error en trabajo {job_id}: {str(e)}")
        tasks_status[job_id]["status"] = "failed"
        tasks_status[job_id]["error_message"] = str(e)

@app.get("/")
async def read_root():
    return RedirectResponse(url="/docs", status_code=302)

@app.get("/apidocs.json")
async def get_apidocs():
    """
    Retorna la documentación de la API en formato JSON (OpenAPI 3.0.0).
    """
    with open("apidocs.json", "r", encoding="utf-8") as f:
        return json.load(f)

@app.post("/text-to-speech", response_model=TextToSpeechResponse)
async def text_to_speech(request: TextToSpeechRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    tasks_status[job_id] = {"status": "queued", "errors": [], "output_file": None, "audio_url": None}
    background_tasks.add_task(
        process_text_to_speech,
        request.text,
        request.reference_audio,
        request.exaggeration,
        request.temperature,
        request.seed,
        request.cfg_weight,
        job_id,
    )
    return TextToSpeechResponse(job_id=job_id, status="queued", audio_url=f"/status/{job_id}")

@app.get("/status/{job_id}", response_model=dict)
def get_job_status(job_id: str):
    if job_id not in tasks_status:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return tasks_status[job_id]

@app.get("/audio/{job_id}")
async def get_audio(job_id: str):
    if job_id not in tasks_status:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if tasks_status[job_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Audio aún no listo. Estado: {tasks_status[job_id]['status']}")
    output_file = tasks_status[job_id]["output_file"]
    if not output_file or not os.path.exists(output_file):
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(path=output_file, media_type="audio/mpeg", filename=f"audio_{job_id}.mp3")

def stream_audio_file(path: str, chunk_size: int = 8192):
    """Generador para hacer streaming de un archivo de audio y eliminarlo al terminar."""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        if os.path.exists(path):
            os.remove(path)

@app.post("/tts_stream")
async def tts_stream(request: TTSStreamRequest):
    """
    Endpoint de streaming directo para Text-to-Speech con Chatterbox.

    Parameters:
    - text: Texto a convertir
    - reference_audio: Audio de referencia opcional (por defecto se usa el de la interfaz)
    - exaggeration: Nivel de exageración (por defecto 0.5)
    - temperature: Temperatura de muestreo (por defecto 0.8)
    - seed: Semilla aleatoria (0 para aleatoria)
    - cfg_weight: Peso de CFG (por defecto 0.5)

    Returns:
    - Stream de audio en formato WAV
    """
    try:
        reference_audio = resolve_reference_audio(request.reference_audio)
        stream_filename = f"{Config.TEMP_DIR}/stream_{uuid.uuid4()}.wav"

        generate_chatterbox_audio(
            request.text,
            reference_audio,
            request.exaggeration,
            request.temperature,
            request.seed,
            request.cfg_weight,
            stream_filename,
        )

        return StreamingResponse(
            stream_audio_file(stream_filename),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=audio.wav"}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en endpoint /tts_stream: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error procesando solicitud: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5009)
