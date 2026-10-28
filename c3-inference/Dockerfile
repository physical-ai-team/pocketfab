FROM python:3.11-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# TODO(SAM 2 모드): CPU/GPU 전환은 빌드 인자로 한다 (api-spec 6장)
#   ARG TORCH_VARIANT=cpu
#   RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/${TORCH_VARIANT}
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
