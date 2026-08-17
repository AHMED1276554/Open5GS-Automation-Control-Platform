FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    libsctp-dev \
    lksctp-tools \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
COPY sctp_proxy.py .

EXPOSE 38412/sctp
EXPOSE 8000/tcp

CMD ["python", "sctp_proxy.py"]
