FROM python:3.12-slim

RUN apt update && apt install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/myuser/venv/bin:$PATH"
    
RUN python -m venv /home/myuser/venv && \
/home/myuser/venv/bin/python -m pip install --no-cache-dir --upgrade pip

COPY ./app/requirements.txt /app/

RUN pip install --no-cache-dir -r /app/requirements.txt

RUN useradd --create-home --shell /bin/bash myuser

USER myuser

COPY --chown=myuser:myuser ./app /app

WORKDIR /app

EXPOSE 5009

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5009"]
