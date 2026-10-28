FROM python:3.11-slim

# SAM 2를 GitHub에서 설치하므로 git이 필요하다
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# CPU/GPU 전환은 빌드 인자로 한다 (api-spec 6장). 기본은 CPU용 torch(용량이 훨씬 작다).
ARG TORCH_VARIANT=cpu
RUN pip install --no-cache-dir torch==2.14.1 torchvision==0.29.1 \
    --index-url https://download.pytorch.org/whl/${TORCH_VARIANT}

COPY requirements.txt .
# SAM2_BUILD_CUDA=0: GPU 확장 모듈을 빌드하지 않는다 (없어도 추론은 동작)
# --no-build-isolation: SAM 2 빌드에 위에서 설치한 torch를 쓴다. 이게 없으면 pip이 빌드용으로
#   GPU판 torch(수 GB)를 따로 내려받아 빌드가 매우 오래 걸린다.
RUN pip install --no-cache-dir setuptools wheel \
    && SAM2_BUILD_CUDA=0 SAM2_BUILD_ALLOW_ERRORS=1 pip install --no-cache-dir --no-build-isolation -r requirements.txt
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
