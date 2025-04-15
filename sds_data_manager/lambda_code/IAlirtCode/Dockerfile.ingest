FROM public.ecr.aws/lambda/python:3.12

# Copy requirements.txt and install dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the Lambda code
COPY . .

CMD ["ialirt_ingest.lambda_handler"]
