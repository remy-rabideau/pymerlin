FROM python:3.11-slim
COPY . /pymerlin-pkg
RUN pip install /pymerlin-pkg --quiet --no-cache-dir
