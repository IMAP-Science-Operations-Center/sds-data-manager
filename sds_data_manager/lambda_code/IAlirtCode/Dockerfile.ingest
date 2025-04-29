FROM public.ecr.aws/lambda/python:3.12

COPY processing/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY IAlirtCode/ialirt_ingest.py .

CMD ["ialirt_ingest.lambda_handler"]
