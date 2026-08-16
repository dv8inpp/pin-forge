FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p static/generated data

# Run as a non-root user -- if the app or a dependency is ever compromised,
# this limits what the process can touch on the host/container.
RUN useradd --create-home --shell /bin/false pinforge \
    && chown -R pinforge:pinforge /app
USER pinforge

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "60", "app:app"]
