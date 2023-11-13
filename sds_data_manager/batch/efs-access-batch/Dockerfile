FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y git

RUN python -m pip install --upgrade pip

COPY requirements.txt .

RUN python -m pip install -r requirements.txt

COPY . .

CMD ["python", "read_from_efs.py" ]
