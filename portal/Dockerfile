FROM python:3.13-slim
WORKDIR /app
COPY portal/app.py ./app.py
COPY portal/static ./static
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "app.py"]
